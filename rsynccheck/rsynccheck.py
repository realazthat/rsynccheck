import enum
import json
import shlex
import subprocess
import sys
import textwrap
import time
import traceback
from collections import defaultdict
from concurrent.futures import (FIRST_COMPLETED, Future, ThreadPoolExecutor,
                                wait)
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Dict, Generator, List, NamedTuple, Optional, Sequence,
                    Set, TextIO, Tuple, Union)

import numpy as np
import pathspec
import yaml
from prettytable import PrettyTable
from pydantic import BaseModel
from rich.console import Console
from typing_extensions import Literal

_VALID_FILE_ITER_METHODS = ('iterdir', 'git', 'auto')
_FileIterMethodLiteral = Literal['iterdir', 'git', 'auto']
_VALID_FORMATS = ('fraction', 'percent', 'json', 'json-compact', 'table',
                  'yaml', 'yaml-debug', 'none')
_FormatLiteral = Literal['fraction', 'decimal', 'json', 'json-compact', 'table',
                         'yaml', 'yaml-debug', 'none']


class _HashError(Exception):
  pass


class _FileNotFoundError(_HashError):
  pass


class _ChunkNotFoundError(_HashError):
  pass


class _HashInvokeError(_HashError):
  pass


class _HashParseError(_HashInvokeError):
  pass


class AuditFileSchema(BaseModel):

  class UnusedMeta(BaseModel):
    dd_cmd: str
    hash_shell_str: str
    group_size: int
    directory: str
    file_iter_method: _FileIterMethodLiteral
    ignored: List[str]
    max_workers: int
    ignore_metas: Dict[str, List[str]]

  files: Dict[str, List[str]]
  chunk_size: int
  meta_unused: UnusedMeta


def _Ignore(*, rel_path: Path, ignores: List[pathspec.PathSpec]) -> bool:
  return any(ignore.match_file(str(rel_path)) for ignore in ignores)


class _ExecuteError(Exception):

  def __init__(
      self,
      msg: str,
      *,
      cmd: Union[List[str], str],
      cmd_str: str,
      cwd: Path,
      returncode: Optional[int],
  ):
    self.cmd = cmd
    self.cmd_str = cmd_str
    self.cwd = cwd
    self.returncode = returncode
    super().__init__(msg)


def _Execute(*,
             cmd: Union[List[str], str],
             cwd: Path,
             console: Optional[Console],
             expected_error_status: Sequence[int] = [0]) -> str:
  is_shell_cmd = isinstance(cmd, str)
  cmd_str = cmd if is_shell_cmd else shlex.join(cmd)
  try:
    if console is not None:
      console.print(f'Executing: {cmd_str}', style='bold blue')
    output = subprocess.check_output(cmd,
                                     cwd=str(cwd),
                                     stderr=subprocess.PIPE,
                                     shell=is_shell_cmd)
    return output.decode('utf-8')
  except subprocess.CalledProcessError as e:
    if e.returncode in expected_error_status:
      return e.output.decode('utf-8')
    msg = f'Failed to run {json.dumps(cmd_str)}'
    msg += f'\n  Error: {json.dumps(str(e))}'
    msg += f'\n  Command: {json.dumps(shlex.join(cmd))}'
    if e.stderr:
      msg += f'\n  stderr:\n{textwrap.indent(e.stderr.decode("utf-8"), "    ")}'
    if e.stdout:
      msg += f'\n  stdout:\n{textwrap.indent(e.stdout.decode("utf-8"), "    ")}'
    raise _ExecuteError(msg,
                        cmd=cmd,
                        cmd_str=cmd_str,
                        cwd=cwd,
                        returncode=e.returncode) from e


class _PathList(NamedTuple):
  directory: Path
  rel_paths: List[Path]
  abs_paths: List[Path]
  ignored: List[Path]


def _GetPathsViaIterDir(*, abs_directory: Path,
                        ignores: List[pathspec.PathSpec]) -> _PathList:
  abs_paths: List[Path] = []
  ignored: List[Path] = []
  tovisit: List[Path] = [abs_directory]
  while tovisit:
    tovisit_path = tovisit.pop()
    for abs_child in tovisit_path.iterdir():
      rel_child = abs_child.relative_to(abs_directory)
      if _Ignore(rel_path=rel_child, ignores=ignores):
        ignored.append(abs_child)
        continue
      if abs_child.is_dir():
        tovisit.append(abs_child)
      else:
        abs_paths.append(abs_child)
  rel_paths = [path.relative_to(abs_directory) for path in abs_paths]
  ignored = [path.relative_to(abs_directory) for path in ignored]
  return _PathList(directory=abs_directory,
                   abs_paths=abs_paths,
                   rel_paths=rel_paths,
                   ignored=ignored)


def _GetPathsViaGit(*, directory: Path, ignores: List[pathspec.PathSpec],
                    console: Console) -> _PathList:
  cmd = ['git', 'ls-files']
  output: str = _Execute(cmd=cmd, cwd=directory, console=console)
  abs_paths: List[Path] = []
  rel_paths: List[Path] = []
  ignored: List[Path] = []
  for line in output.splitlines():
    line = line.strip()
    if len(line) == 0:
      continue
    rel_path = Path(line)
    if not rel_path.exists():
      raise Exception(
          f'git ls-files gave a file that does not exist, line={line}, path.exists(): {rel_path.exists()}'
      )
    if _Ignore(rel_path=rel_path, ignores=ignores):
      ignored.append(rel_path)
      continue
    abs_paths.append(directory / rel_path)
    rel_paths.append(rel_path)
  return _PathList(directory=directory,
                   abs_paths=abs_paths,
                   rel_paths=rel_paths,
                   ignored=ignored)


def _GetPaths(*, directory: Path, file_iter_method: _FileIterMethodLiteral,
              ignores: List[pathspec.PathSpec], console: Console) -> _PathList:
  if file_iter_method == 'iterdir':
    return _GetPathsViaIterDir(abs_directory=directory, ignores=ignores)
  elif file_iter_method == 'git':
    return _GetPathsViaGit(directory=directory,
                           ignores=ignores,
                           console=console)
  elif file_iter_method == 'auto':
    git_dir = directory / '.git'
    if git_dir.exists():
      return _GetPathsViaGit(directory=directory,
                             ignores=ignores,
                             console=console)
    else:
      return _GetPathsViaIterDir(abs_directory=directory, ignores=ignores)
  else:
    raise Exception(
        f'Invalid file_iter_method, file_iter_method={file_iter_method}, valid file_iter_methods={_VALID_FILE_ITER_METHODS}'
    )


class _JobRequestGroup(BaseModel):
  abs_path: Path
  rel_path: Path
  chunk_idx: List[int]


class _JobResultGroup(BaseModel):
  abs_path: Path
  rel_path: Path
  chunk_idx: List[int]
  hash: List[str]


class _ResultType(enum.Enum):
  SUCCESS = enum.auto()
  FAIL_HASH = enum.auto()
  FAIL_MATCH = enum.auto()
  FAIL_USER_ERROR = enum.auto()
  FAIL_OTHER_ERROR = enum.auto()


@dataclass
class _ChunkStatus:
  type: _ResultType
  message: str
  rel_path: Path
  chunk_idx: int
  req_group: Optional[_JobRequestGroup] = None
  res_group: Optional[_JobResultGroup] = None
  exception: Optional[Exception] = None


class _GeneralFailure(NamedTuple):
  message: str
  rel_path: Optional[Path] = None
  chunk_idx: Optional[int] = None
  req_group: Optional[_JobRequestGroup] = None
  res_group: Optional[_JobResultGroup] = None
  exception: Optional[Exception] = None


class _ProgressInfoSummary(BaseModel):
  success: int
  fail_hash: int
  fail_match: int
  fail_user_error: int
  fail_other_error: int
  chunks: int


@dataclass
class _ProgressInfo:
  results: List[_ChunkStatus]
  chunks: int

  def Combine(self, other: '_ProgressInfo'):
    if not self.chunks == other.chunks:
      raise AssertionError(
          f'chunks mismatch: self.chunks={self.chunks}, other.chunks={other.chunks}'
      )
    self.results.extend(other.results)

  def Summary(self) -> _ProgressInfoSummary:
    success = 0
    fail_hash = 0
    fail_match = 0
    fail_user_error = 0
    fail_other_error = 0
    for result in self.results:
      if result.type == _ResultType.SUCCESS:
        success += 1
      elif result.type == _ResultType.FAIL_HASH:
        fail_hash += 1
      elif result.type == _ResultType.FAIL_MATCH:
        fail_match += 1
      elif result.type == _ResultType.FAIL_USER_ERROR:
        fail_user_error += 1
      elif result.type == _ResultType.FAIL_OTHER_ERROR:
        fail_other_error += 1
      else:
        raise AssertionError(
            f'Unknown result type: {result.type}, result={result}')
    return _ProgressInfoSummary(success=success,
                                fail_hash=fail_hash,
                                fail_match=fail_match,
                                fail_user_error=fail_user_error,
                                fail_other_error=fail_other_error,
                                chunks=self.chunks)


def _Format(*, progress: _ProgressInfo, format: _FormatLiteral) -> str:
  if format == 'yaml':
    return yaml.safe_dump(progress.Summary().model_dump(mode='json',
                                                        round_trip=True),
                          sort_keys=False)
  elif format == 'json':
    return json.dumps(progress.Summary().model_dump(mode='json',
                                                    round_trip=True),
                      indent=2)
  elif format == 'json-compact':
    return json.dumps(progress.Summary().model_dump(mode='json',
                                                    round_trip=True),
                      indent=None)
  elif format == 'table':
    table = PrettyTable(["Name", "Value"])

    for name, value in progress.Summary().model_dump(mode='json',
                                                     round_trip=True).items():
      table.add_row([name, value])

    return str(table)
  elif format == 'fraction':
    return f'{progress.Summary().success}/{progress.chunks}'
  elif format == 'percent':
    complete = np.divide(progress.Summary().success * 100., progress.chunks)
    return f'{complete:.2f}%'
  elif format == 'none':
    return ''
  elif format == 'yaml-debug':
    data_dict = {
        'progress': progress.Summary().model_dump(mode='json', round_trip=True),
        'chunks': progress.chunks,
        'results': []
    }
    for result in progress.results:
      result_dict = {
          'type':
          result.type.name,
          'message':
          result.message,
          'rel_path':
          str(result.rel_path),
          'chunk_idx':
          result.chunk_idx,
          'req_group':
          None if result.req_group is None else result.req_group.model_dump(
              mode='json', round_trip=True),
          'res_group':
          None if result.res_group is None else result.res_group.model_dump(
              mode='json', round_trip=True),
          'exception':
          None if result.exception is None else str(result.exception)
      }
      data_dict['results'].append(result_dict)
    return yaml.safe_dump(data_dict, sort_keys=False)
  else:
    raise Exception(
        f'Invalid format, format={format}, valid formats={_VALID_FORMATS}')


def _FileSize(*, path: Path) -> int:
  return path.stat().st_size


def _GetNumChunks(file_size: int, chunk_size: int) -> int:
  return (file_size // chunk_size) + (1 if file_size % chunk_size != 0 else 0)


def _EnumerateFileJobReqGroups(*, rel_path: Path, abs_path: Path,
                               chunk_size: int,
                               group_size: int) -> List[_JobRequestGroup]:
  size = _FileSize(path=abs_path)
  num_chunks = (size // chunk_size) + (1 if size % chunk_size != 0 else 0)
  groups: List[_JobRequestGroup] = []
  for chunk_idx in range(num_chunks):
    if len(groups) == 0 or len(groups[-1].chunk_idx) >= group_size:
      groups.append(
          _JobRequestGroup(rel_path=rel_path, abs_path=abs_path, chunk_idx=[]))
    groups[-1].chunk_idx.append(chunk_idx)
  return groups


def _EnumerateAllJobReqGroups(*, rel_paths: List[Path], abs_paths: List[Path],
                              chunk_size: int,
                              group_size: int) -> List[_JobRequestGroup]:
  groups: List[_JobRequestGroup] = []
  for rel_path, abs_path in zip(rel_paths, abs_paths):
    groups.extend(
        _EnumerateFileJobReqGroups(rel_path=rel_path,
                                   abs_path=abs_path,
                                   chunk_size=chunk_size,
                                   group_size=group_size))
  return groups


def _HashChunk(*, dd_cmd: str, hash_shell_str: str, directory: Path, path: Path,
               chunk_idx: int, chunk_size: int) -> str:
  if not path.exists():
    raise _FileNotFoundError(f'File not found: {path}')

  size = _FileSize(path=path)
  file_chunks = _GetNumChunks(file_size=size, chunk_size=chunk_size)
  if chunk_idx >= file_chunks:
    raise _ChunkNotFoundError(
        f'Chunk index {chunk_idx} is greater than the number of chunks in file'
        f'\n  path: {path}'
        f'\n  chunk_idx: {chunk_idx}'
        f'\n  chunk_size: {chunk_size}'
        f'\n  file size: {size}'
        f'\n  file chunks: {file_chunks}')
  dd_invoc = [
      dd_cmd, f'if={path}', f'bs={chunk_size}', f'skip={chunk_idx}', f'count=1'
  ]
  dd_invoc_str = shlex.join(dd_invoc)
  shell: str = f'({dd_invoc_str}) | {hash_shell_str}'
  try:
    output = _Execute(cmd=shell, cwd=directory, console=None)
  except Exception as e:
    raise _HashInvokeError(
        f'Failed to hash chunk: ({type(e).__name__}) {str(e)}') from e
  parts = output.split()
  if len(parts) != 2:
    raise _HashParseError(
        f'Expected 2 parts in hash output, got {len(parts)}: {json.dumps(output)}'
    )
  hash_str = parts[0]
  return hash_str.strip()


def _ExecuteJobGroup(*, dd_cmd: str, hash_shell_str: str, directory: Path,
                     req_group: _JobRequestGroup,
                     chunk_size: int) -> _JobResultGroup:
  res_group: _JobResultGroup = _JobResultGroup(abs_path=req_group.abs_path,
                                               rel_path=req_group.rel_path,
                                               chunk_idx=[],
                                               hash=[])
  chunk_idx: int
  for chunk_idx in req_group.chunk_idx:
    hash_str = _HashChunk(dd_cmd=dd_cmd,
                          hash_shell_str=hash_shell_str,
                          directory=directory,
                          path=req_group.abs_path,
                          chunk_idx=chunk_idx,
                          chunk_size=chunk_size)
    res_group.chunk_idx.append(chunk_idx)
    res_group.hash.append(hash_str)
  return res_group


def _HashGroupsInFuture(
    *, dd_cmd: str, hash_shell_str: str, directory: Path,
    req_groups: List[_JobRequestGroup], chunk_size: int, max_workers: int
) -> Generator[Tuple[_JobRequestGroup, 'Future[_JobResultGroup]'], None, None]:
  fut2req: Dict['Future[_JobResultGroup]', _JobRequestGroup] = {}
  futures: List['Future[_JobResultGroup]'] = []
  running: Set['Future[_JobResultGroup]'] = set()

  with ThreadPoolExecutor(max_workers=max_workers) as executor:
    for req_group in req_groups:
      fut = executor.submit(_ExecuteJobGroup,
                            dd_cmd=dd_cmd,
                            hash_shell_str=hash_shell_str,
                            directory=directory,
                            req_group=req_group,
                            chunk_size=chunk_size)
      fut2req[fut] = req_group

      running.add(fut)
      futures.append(fut)
      if len(running) >= max_workers:
        done, running = wait(running, return_when=FIRST_COMPLETED)
        for fut in done:
          yield (fut2req[fut], fut)

  while len(running) > 0:
    done, running = wait(running, return_when=FIRST_COMPLETED)
    for fut in done:
      yield (fut2req[fut], fut)


def _MeasureHashMatches(*, req_group: _JobRequestGroup,
                        res_group: _JobResultGroup, audit: AuditFileSchema,
                        chunks: int) -> _ProgressInfo:
  progress = _ProgressInfo(results=[], chunks=chunks)
  for i in range(len(res_group.chunk_idx)):
    path_str = str(res_group.rel_path)
    if path_str not in audit.files:
      raise AssertionError(
          f'path_str={path_str} not in audit.files: {json.dumps(list(audit.files.keys()))}'
      )

    audit_chunks: List[str] = audit.files[path_str]
    if not i < len(audit_chunks):
      raise AssertionError(
          f'Index {i} is out of range for audit_chunks: {json.dumps(audit_chunks)}'
      )
    expected_hash = audit_chunks[i]
    actual_hash = res_group.hash[i]
    if expected_hash != actual_hash:
      progress.results.append(
          _ChunkStatus(
              type=_ResultType.FAIL_MATCH,
              message=f'Hash mismatch: {expected_hash} != {actual_hash}',
              rel_path=res_group.rel_path,
              chunk_idx=res_group.chunk_idx[i],
              req_group=req_group,
              res_group=res_group,
              exception=None))
      continue

    progress.results.append(
        _ChunkStatus(type=_ResultType.SUCCESS,
                     message='Hash matches',
                     rel_path=res_group.rel_path,
                     chunk_idx=res_group.chunk_idx[i],
                     req_group=req_group,
                     res_group=res_group,
                     exception=None))
  return progress


def _HashGroups(
    *, dd_cmd: str, hash_shell_str: str, directory: Path,
    req_groups: List[_JobRequestGroup], chunk_size: int, max_workers: int,
    progress: _ProgressInfo, show_progress: Optional[_FormatLiteral],
    audit: Optional[AuditFileSchema], console: Console
) -> Generator[Tuple[_JobRequestGroup, Union[_JobResultGroup, _GeneralFailure]],
               None, None]:

  progress.results = []
  progress.chunks = sum(len(req_group.chunk_idx) for req_group in req_groups)

  req_group: _JobRequestGroup
  res_group_fut: Future['_JobResultGroup']

  last_progress_t = time.time()
  for (req_group,
       res_group_fut) in _HashGroupsInFuture(dd_cmd=dd_cmd,
                                             hash_shell_str=hash_shell_str,
                                             directory=directory,
                                             req_groups=req_groups,
                                             chunk_size=chunk_size,
                                             max_workers=max_workers):
    try:
      res_group = res_group_fut.result()
      if audit is not None:
        progress_ = _MeasureHashMatches(req_group=req_group,
                                        res_group=res_group,
                                        audit=audit,
                                        chunks=progress.chunks)
        progress.Combine(progress_)
      else:
        for chunk_idx in res_group.chunk_idx:
          progress.results.append(
              _ChunkStatus(type=_ResultType.SUCCESS,
                           message='Hash success',
                           rel_path=res_group.rel_path,
                           chunk_idx=chunk_idx,
                           req_group=req_group,
                           res_group=res_group,
                           exception=None))
      yield (req_group, res_group)
    except Exception as e:
      for chunk_idx in req_group.chunk_idx:
        progress.results.append(
            _ChunkStatus(
                type=_ResultType.FAIL_OTHER_ERROR,
                message=f'Failed to hash chunk: ({type(e).__name__}) {str(e)}',
                rel_path=req_group.rel_path,
                chunk_idx=chunk_idx,
                req_group=req_group,
                res_group=None,
                exception=e))
      failure = _GeneralFailure(
          message=f'Failed to hash file: ({type(e).__name__}) {str(e)}',
          req_group=req_group,
          exception=e)
      yield (req_group, failure)

    if show_progress is not None:
      if time.time() - last_progress_t > 2:
        console.print(_Format(progress=progress, format=show_progress))
        last_progress_t = time.time()


def _CheckFailFailures(*, failures: List[_GeneralFailure], console: Console):
  if len(failures) == 0:
    return

  console.print('Failures:', len(failures), style='bold red')
  for failure in failures:
    console.print('Failure:', style='bold red')
    if failure.message:
      console.print(textwrap.indent(failure.message, '  '), style='bold red')
    if failure.req_group:
      console.print('  at:', failure.req_group.rel_path, style='bold red')
    if failure.exception:
      console.print(
          f'  Exception ({type(failure.exception).__name__}):\n{textwrap.indent(str(failure.exception), "    ")}',
          style='bold red')
      __traceback__ = failure.exception.__traceback__
      if __traceback__ is not None:
        console.print('  Exception trace:', style='bold red')
        for (frame, _) in traceback.walk_tb(__traceback__):
          console.print(f'    {frame.f_code.co_filename}:{frame.f_lineno}',
                        style='bold red')
    # console.print(Traceback.from_exception(type(failure.exception), exc_value=failure.exception, traceback=failure.exception.__traceback__))

  console.print(f'{"-"*80}', style='bold red')
  console.print('Failures:', len(failures), style='bold red')
  console.print('Exiting due to failures', style='bold red')
  sys.exit(1)


def _FindIgnoreFile(cwd: Path) -> Optional[Path]:
  """
  Search for a '.rsynccheck-ignore' file in the current working directory and its ancestors.

  Returns:
      Optional[Path]: The path to the found '.rsynccheck-ignore' file, or None
        if not found.
  """
  for directory in [cwd] + list(cwd.parents):
    ignorefile = directory / '.rsynccheck-ignore'
    if ignorefile.exists() and ignorefile.is_file():
      return ignorefile
  return None


def _ConstructIgnorePathSpecs(*, ignorefiles: List[TextIO],
                              ignorelines: List[str],
                              ignore_metas: Dict[str, List[str]],
                              cwd: Path) -> List[pathspec.PathSpec]:

  found_ignore_file: Optional[TextIO] = None
  try:
    found_ignore_file_path: Optional[Path] = _FindIgnoreFile(cwd=cwd)
    if found_ignore_file_path is not None:
      found_ignore_file = found_ignore_file_path.open('r')
      ignorefiles.append(found_ignore_file)

    ignores = []
    ignorefile: TextIO
    for ignorefile in ignorefiles:
      ignorefile_contents = ignorefile.read()
      ignorefile_lines: List[str] = ignorefile_contents.splitlines()
      ignore_metas[ignorefile.name] = ignorefile_lines
      ignores.append(
          pathspec.PathSpec.from_lines('gitwildmatch', ignorefile_lines))
    ignore_metas['~ignorelines'] = ignorelines
    ignores.append(pathspec.PathSpec.from_lines('gitwildmatch', ignorelines))
    return ignores
  finally:
    if found_ignore_file is not None:
      found_ignore_file.close()


class HashResults(NamedTuple):
  rel_paths: List[Path]
  abs_paths: List[Path]
  failures: List[_GeneralFailure]
  path2hashes: Dict[str, List[str]]
  progress: _ProgressInfo


def _CheckFailValidateReqGroups(req_groups: List[_JobRequestGroup]):
  seen: Dict[Tuple[Path, int], int] = {}

  for idx, req_group in enumerate(req_groups):
    for chunk_idx in req_group.chunk_idx:
      key = (req_group.rel_path, chunk_idx)
      if key in seen:
        raise AssertionError(
            f'Duplicate chunk index: {key}, seen at {seen[key]} and {idx}')
      seen[key] = idx


def Hash(dd_cmd: str, hash_shell_str: str, directory: Path,
         rel_paths: List[Path], abs_paths: List[Path],
         req_groups: List[_JobRequestGroup], chunk_size: int, max_workers: int,
         show_progress: Optional[_FormatLiteral],
         console: Console) -> HashResults:
  _CheckFailValidateReqGroups(req_groups)

  progress = _ProgressInfo(
      results=[],
      chunks=sum(len(req_group.chunk_idx) for req_group in req_groups))
  results: List[Tuple[_JobRequestGroup,
                      Union[_JobResultGroup, _GeneralFailure]]] = list(
                          _HashGroups(dd_cmd=dd_cmd,
                                      hash_shell_str=hash_shell_str,
                                      directory=directory,
                                      req_groups=req_groups,
                                      chunk_size=chunk_size,
                                      max_workers=max_workers,
                                      progress=progress,
                                      show_progress=show_progress,
                                      audit=None,
                                      console=console))

  if not len(req_groups) == len(results):
    raise AssertionError(
        f'len(req_groups)={len(req_groups)} != len(results)={len(results)}')

  failures: List[_GeneralFailure] = [
      res_group for _, res_group in results
      if isinstance(res_group, _GeneralFailure)
  ]

  path2hash_tups: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
  res_group_or: Union[_JobResultGroup, _GeneralFailure]
  for req_group, res_group_or in results:
    if isinstance(res_group_or, _GeneralFailure):
      path2hash_tups[str(req_group.rel_path)] += [(ci, 'FAILURE')
                                                  for ci in req_group.chunk_idx]
      continue
    assert req_group.chunk_idx == res_group_or.chunk_idx
    assert req_group.rel_path == res_group_or.rel_path
    path2hash_tups[str(req_group.rel_path)] += [
        (ci, h) for ci, h in zip(res_group_or.chunk_idx, res_group_or.hash)
    ]

  path2hashes: Dict[str, List[str]] = {}
  # (chunk index, hash) tuples.
  ci_h_tuples: List[Tuple[int, str]]
  for path_str, ci_h_tuples in path2hash_tups.items():
    ci_h_tuples = sorted(ci_h_tuples, key=lambda x: x[0])
    # Ensure the indices of the tuples match the list indices:
    if not all(ci == i for i, (ci, _) in enumerate(ci_h_tuples)):
      raise AssertionError(
          f'Indices of ci_h_tuples do not match list indices: {json.dumps(ci_h_tuples)}'
      )
    hashes: List[str] = [h for _, h in ci_h_tuples]
    path2hashes[path_str] = hashes

  mismatched_files: Set[str] = set(path2hashes.keys()) ^ set(map(
      str, rel_paths))
  if mismatched_files:
    for rel_path in mismatched_files:
      failures.append(
          _GeneralFailure(
              message=f'File in path2hashes but not in rel_paths: {rel_path}',
              rel_path=Path(rel_path),
              chunk_idx=None,
              req_group=None,
              exception=None))

  return HashResults(rel_paths=rel_paths,
                     abs_paths=abs_paths,
                     failures=failures,
                     path2hashes=path2hashes,
                     progress=progress)


def HashMain(*, dd_cmd: str, hash_shell_str: str, directory: Path,
             file_iter_method: _FileIterMethodLiteral, audit_file_path: Path,
             ignores: List[pathspec.PathSpec], ignore_metas: Dict[str,
                                                                  List[str]],
             chunk_size: int, group_size: int, max_workers: int,
             console: Console, show_progress: Optional[_FormatLiteral]):

  paths: _PathList = _GetPaths(directory=directory,
                               file_iter_method=file_iter_method,
                               ignores=ignores,
                               console=console)
  for path in paths.rel_paths:
    console.print(f'Found: {path}', style='bold blue')

  req_groups = _EnumerateAllJobReqGroups(rel_paths=paths.rel_paths,
                                         abs_paths=paths.abs_paths,
                                         chunk_size=chunk_size,
                                         group_size=group_size)
  result = Hash(dd_cmd=dd_cmd,
                hash_shell_str=hash_shell_str,
                directory=directory,
                rel_paths=paths.rel_paths,
                abs_paths=paths.abs_paths,
                req_groups=req_groups,
                chunk_size=chunk_size,
                max_workers=max_workers,
                show_progress=show_progress,
                console=console)
  audit = AuditFileSchema(files={},
                          chunk_size=chunk_size,
                          meta_unused=AuditFileSchema.UnusedMeta(
                              dd_cmd=dd_cmd,
                              hash_shell_str=hash_shell_str,
                              group_size=group_size,
                              directory=str(directory),
                              file_iter_method=file_iter_method,
                              ignored=list(map(str, ignores)),
                              max_workers=max_workers,
                              ignore_metas=ignore_metas,
                          ))
  for path_str, hashes in result.path2hashes.items():
    audit.files[path_str] = hashes

  _CheckFailFailures(failures=result.failures, console=console)

  ##############################################################################
  # Write the audit file.
  if not audit_file_path.parent.exists():
    audit_file_path.parent.mkdir(parents=True, exist_ok=True)
  with audit_file_path.open('w') as audit_file:
    yaml.safe_dump(audit.model_dump(mode='json', round_trip=True),
                   audit_file,
                   sort_keys=False)
  ##############################################################################
  console.print('Hashing complete', style='bold green')


def AuditMain(*, dd_cmd: str, hash_shell_str: str, directory: Path,
              audit_file_path: Path, show_progress: Optional[_FormatLiteral],
              output_format: _FormatLiteral, mismatch_exit: int,
              group_size: int, max_workers: int, console: Console):
  with audit_file_path.open('r') as audit_file:
    audit_dict: Dict[str, Any] = yaml.safe_load(audit_file)
  audit: AuditFileSchema = AuditFileSchema.model_validate(audit_dict)

  ##############################################################################
  chunk_size: int = audit.chunk_size
  rel_paths: List[Path] = [Path(path_str) for path_str in audit.files.keys()]
  abs_paths: List[Path] = [
      directory / Path(path_str) for path_str in audit.files.keys()
  ]
  ##############################################################################
  # Construct jobs for hashing.
  req_groups: List[_JobRequestGroup] = []
  for path_str, hashes in audit.files.items():
    abs_path = directory / Path(path_str)
    rel_path = Path(path_str)

    for chunk_idx in range(len(hashes)):
      # If there is no previous job group, or the previous job group is full,
      # or the previous job group is for a different file, then create a new
      # job group.
      if (len(req_groups) == 0 or len(req_groups[-1].chunk_idx) >= group_size
          or req_groups[-1].rel_path != rel_path):
        req_groups.append(
            _JobRequestGroup(abs_path=abs_path, rel_path=rel_path,
                             chunk_idx=[]))
      # Now that we know the last job group is valid and compatible, add the
      # chunk index to it.
      req_groups[-1].chunk_idx.append(chunk_idx)
  _CheckFailValidateReqGroups(req_groups)
  ##############################################################################
  # Print out some information about the files we are auditing.
  for path_str, hashes in audit.files.items():
    abs_path = directory / Path(path_str)
    found_str: str
    if not abs_path.exists():
      found_str = 'not found'
    else:
      found_str = 'found'
    console.print(f'Auditing: {path_str} ({found_str})', style='bold blue')
    abs_path = directory / Path(path_str)
  ##############################################################################
  result = Hash(dd_cmd=dd_cmd,
                hash_shell_str=hash_shell_str,
                directory=directory,
                rel_paths=rel_paths,
                abs_paths=abs_paths,
                req_groups=req_groups,
                chunk_size=chunk_size,
                max_workers=max_workers,
                show_progress=show_progress,
                console=console)
  ##############################################################################
  second_audit = audit.model_copy(update={'files': {}}, deep=True)

  for path_str, hashes in result.path2hashes.items():
    second_audit.files[path_str] = hashes
  ##############################################################################
  sys.stderr.flush()
  sys.stdout.flush()
  print(_Format(progress=result.progress, format=output_format))
  sys.stderr.flush()
  sys.stdout.flush()
  ##############################################################################
  # Now there are two ways to check if the audit passes:
  # 1. compare audit.files and second_audit.files.
  # 2. compare progress.Sumarry().success with the number of chunks.

  hashes_match = audit.files == second_audit.files
  finished_with_no_errors = result.progress.Summary(
  ).success == result.progress.chunks

  if hashes_match != finished_with_no_errors:

    raise AssertionError(
        'Internal error: Somehow, the audit passed in one way but not the other:'
        f'\n hashes_match={hashes_match}'
        f'\n finished_with_no_errors={finished_with_no_errors}')

  if hashes_match:
    console.print('Files match!', style='bold green')
    sys.exit(0)
  console.print('Files do not match!', style='bold red')
  sys.exit(mismatch_exit)
