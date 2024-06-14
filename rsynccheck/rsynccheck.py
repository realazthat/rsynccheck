# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
#
# The RSyncCheck project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.

import asyncio
import enum
import io
import json
import logging
import pathlib
import shlex
import string
import sys
import textwrap
import time
import traceback
from asyncio.subprocess import Process
from collections import defaultdict
from concurrent.futures import (FIRST_COMPLETED, Future, ThreadPoolExecutor,
                                wait)
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import (Any, AsyncGenerator, Dict, List, NamedTuple, Optional,
                    Sequence, Set, TextIO, Tuple, Union)

import anyio
import pathspec
import yaml
from prettytable import PrettyTable
from pydantic import BaseModel
from rich.console import Console
from typing_extensions import Literal

from .utilities.error_utils import _ErrorContext, _YamlDump

logger = logging.getLogger(__name__)

_VALID_FILE_ITER_METHODS = ('iterdir', 'git', 'auto')
_FileIterMethodLiteral = Literal['iterdir', 'git', 'auto']
_VALID_FORMATS = ('fraction', 'percent', 'json', 'json-compact', 'table',
                  'yaml', 'yaml-debug', 'none')
_FormatLiteral = Literal['fraction', 'decimal', 'json', 'json-compact', 'table',
                         'yaml', 'yaml-debug', 'none']


class _CopyCheckError(Exception):
  pass


class _HashInternalError(Exception):
  pass


class _FileNotFoundError(_CopyCheckError):
  pass


class _ChunkNotFoundError(_CopyCheckError):
  pass


class _HashInvokeError(_HashInternalError):
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


def _Ignore(*, rel_path: PurePosixPath,
            ignores: List[pathspec.PathSpec]) -> bool:
  return any(ignore.match_file(str(rel_path)) for ignore in ignores)


class _ExecuteError(Exception):

  def __init__(
      self,
      msg: str,
      *,
      cmd: Union[List[str], str],
      cmd_str: str,
      cwd: pathlib.Path,
      returncode: Optional[int],
  ):
    self.cmd = cmd
    self.cmd_str = cmd_str
    self.cwd = cwd
    self.returncode = returncode
    super().__init__(msg)


async def _Execute(*,
                   cmd: List[str],
                   cwd: anyio.Path,
                   console: Optional[Console],
                   expected_error_status: Sequence[int] = [0]) -> str:
  cmd_str: str = cmd if isinstance(cmd, str) else shlex.join(cmd)
  if console is not None:
    console.print(f'Executing: {cmd_str}', style='bold blue')
  logger.debug(f'Executing: {cmd_str}')
  stdout: bytes = b''
  stderr: bytes = b''
  process: Process = await asyncio.create_subprocess_exec(
      *cmd,
      cwd=str(cwd),
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE)

  def _ErrorMsg(e: Optional[Exception]) -> str:
    msg = f'Failed to run {json.dumps(cmd_str)}'
    if e is not None:
      msg += f'\n  Exception: {json.dumps(str(e))}'
    msg += f'\n  Command: {json.dumps(shlex.join(cmd))}'
    msg += f'\n  cwd: {json.dumps(str(cwd))}'
    if len(stderr) > 0:
      msg += f'\n  stderr:\n{textwrap.indent(stderr.decode("utf-8"), "    ")}'
    if len(stdout) > 0:
      msg += f'\n  stdout:\n{textwrap.indent(stdout.decode("utf-8"), "    ")}'
    return msg

  try:
    stdout, stderr = await process.communicate()

    if process.returncode in expected_error_status:
      return stdout.decode('utf-8')

  except (Exception) as e:
    raise _ExecuteError(_ErrorMsg(e),
                        cmd=cmd,
                        cmd_str=cmd_str,
                        cwd=pathlib.Path(cwd),
                        returncode=process.returncode) from e
  raise _ExecuteError(_ErrorMsg(e=None),
                      cmd=cmd,
                      cmd_str=cmd_str,
                      cwd=pathlib.Path(cwd),
                      returncode=process.returncode)


class _PathList(NamedTuple):
  directory: anyio.Path
  rel_paths: List[PurePosixPath]
  ignored: List[PurePosixPath]


async def _GetPathsViaIterDir(*, directory: anyio.Path,
                              ignores: List[pathspec.PathSpec]) -> _PathList:
  # Ignored files, paths concatenated with `directory`.
  ignored: List[PurePosixPath] = []
  # Directories to visit, paths concatenated with `directory`.
  tovisit: List[PurePosixPath] = [
      PurePosixPath(directory.relative_to(directory).as_posix())
  ]
  rel_paths: List[PurePosixPath] = []
  while tovisit:
    tovisit_rel_path: PurePosixPath = tovisit.pop()
    to_visit_path = directory / tovisit_rel_path
    child_path: anyio.Path
    async for child_path in to_visit_path.iterdir():
      child_rel_path = PurePosixPath(
          child_path.relative_to(directory).as_posix())
      if _Ignore(rel_path=child_rel_path, ignores=ignores):
        ignored.append(child_rel_path)
        continue
      if await child_path.is_dir():
        tovisit.append(child_rel_path)
      else:
        rel_paths.append(child_rel_path)
  ignored = [path.relative_to(directory) for path in ignored]
  return _PathList(directory=directory, rel_paths=rel_paths, ignored=ignored)


async def _GetPathsViaGit(*, directory: anyio.Path,
                          ignores: List[pathspec.PathSpec],
                          console: Console) -> _PathList:
  cmd = ['git', 'ls-files']
  output: str = await _Execute(cmd=cmd, cwd=directory, console=console)
  rel_paths: List[PurePosixPath] = []
  ignored: List[PurePosixPath] = []
  for line in output.splitlines():
    line = line.strip()
    if len(line) == 0:
      continue
    rel_path = PurePosixPath(line)
    path: anyio.Path = directory / rel_path
    if not await path.exists():
      raise Exception(
          f'git ls-files gave a file that does not exist, line={line}, path.exists(): {await path.exists()} directory={directory} rel_path={rel_path} path={path}'
      )
    if _Ignore(rel_path=rel_path, ignores=ignores):
      ignored.append(rel_path)
      continue
    rel_paths.append(rel_path)
  return _PathList(directory=directory, rel_paths=rel_paths, ignored=ignored)


async def _GetPaths(*, directory: anyio.Path,
                    file_iter_method: _FileIterMethodLiteral,
                    ignores: List[pathspec.PathSpec],
                    console: Console) -> _PathList:
  if file_iter_method == 'iterdir':
    return await _GetPathsViaIterDir(directory=directory, ignores=ignores)
  elif file_iter_method == 'git':
    return await _GetPathsViaGit(directory=directory,
                                 ignores=ignores,
                                 console=console)
  elif file_iter_method == 'auto':
    git_dir = directory / '.git'
    if await git_dir.exists():
      return await _GetPathsViaGit(directory=directory,
                                   ignores=ignores,
                                   console=console)
    else:
      return await _GetPathsViaIterDir(directory=directory, ignores=ignores)
  else:
    raise Exception(
        f'Invalid file_iter_method, file_iter_method={file_iter_method}, valid file_iter_methods={_VALID_FILE_ITER_METHODS}'
    )


def _SortPaths(*, paths: _PathList) -> _PathList:
  return _PathList(directory=paths.directory,
                   rel_paths=sorted(paths.rel_paths),
                   ignored=paths.ignored)


class _JobRequestGroup(BaseModel):
  rel_path: PurePosixPath
  chunk_idx: List[int]


class _JobResultGroup(BaseModel):
  rel_path: PurePosixPath
  chunk_idx: List[int]
  hash: List[str]


class _ResultType(enum.Enum):
  SUCCESS = enum.auto()
  FAIL_HASH = enum.auto()
  FAIL_MATCH = enum.auto()
  FAIL_COPY_ERROR = enum.auto()
  FAIL_OTHER_ERROR = enum.auto()


@dataclass
class _ChunkStatus:
  type: _ResultType
  message: str
  rel_path: PurePosixPath
  chunk_idx: int
  req_group: Optional[_JobRequestGroup] = None
  res_group: Optional[_JobResultGroup] = None
  exception: Optional[Exception] = None


class _GeneralFailure(NamedTuple):
  message: str
  rel_path: Optional[PurePosixPath] = None
  chunk_idx: Optional[int] = None
  req_group: Optional[_JobRequestGroup] = None
  res_group: Optional[_JobResultGroup] = None
  exception: Optional[Exception] = None


class _ProgressInfoSummary(BaseModel):
  success: int
  fail_hash: int
  fail_match: int
  fail_copy_error: int
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
    fail_copy_error = 0
    fail_other_error = 0
    for result in self.results:
      if result.type == _ResultType.SUCCESS:
        success += 1
      elif result.type == _ResultType.FAIL_HASH:
        fail_hash += 1
      elif result.type == _ResultType.FAIL_MATCH:
        fail_match += 1
      elif result.type == _ResultType.FAIL_COPY_ERROR:
        fail_copy_error += 1
      elif result.type == _ResultType.FAIL_OTHER_ERROR:
        fail_other_error += 1
      else:
        raise AssertionError(
            f'Unknown result type: {result.type}, result={result}')
    return _ProgressInfoSummary(success=success,
                                fail_hash=fail_hash,
                                fail_match=fail_match,
                                fail_copy_error=fail_copy_error,
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
    table = PrettyTable(['Name', 'Value'])

    for name, value in progress.Summary().model_dump(mode='json',
                                                     round_trip=True).items():
      table.add_row([name, value])

    return str(table)
  elif format == 'fraction':
    return f'{progress.Summary().success}/{progress.chunks}'
  elif format == 'percent':
    success = progress.Summary().success
    try:
      complete: float = (success * 100.) / progress.chunks
    except ZeroDivisionError:
      complete = 0
    except (OverflowError):
      complete = 0
    return f'{complete:.2f}%'
  elif format == 'none':
    return ''
  elif format == 'yaml-debug':
    results_list: List[dict] = []
    data_dict = {
        'progress': progress.Summary().model_dump(mode='json', round_trip=True),
        'chunks': progress.chunks,
        'results': results_list
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
      results_list.append(result_dict)
    return yaml.safe_dump(data_dict, sort_keys=False)
  else:
    raise Exception(
        f'Invalid format, format={format}, valid formats={_VALID_FORMATS}')


async def _FileSize(*, path: anyio.Path) -> int:
  return (await path.stat()).st_size


def _GetNumChunks(file_size: int, chunk_size: int) -> int:
  return (file_size // chunk_size) + (1 if file_size % chunk_size != 0 else 0)


async def _EnumerateFileJobReqGroups(*, directory: anyio.Path,
                                     rel_path: PurePosixPath, chunk_size: int,
                                     group_size: int) -> List[_JobRequestGroup]:
  path: anyio.Path = directory / rel_path
  size = await _FileSize(path=path)
  num_chunks = (size // chunk_size) + (1 if size % chunk_size != 0 else 0)
  groups: List[_JobRequestGroup] = []
  for chunk_idx in range(num_chunks):
    if len(groups) == 0 or len(groups[-1].chunk_idx) >= group_size:
      groups.append(_JobRequestGroup(rel_path=rel_path, chunk_idx=[]))
    groups[-1].chunk_idx.append(chunk_idx)
  return groups


async def _EnumerateAllJobReqGroups(*, directory: anyio.Path,
                                    rel_paths: List[PurePosixPath],
                                    chunk_size: int,
                                    group_size: int) -> List[_JobRequestGroup]:
  groups: List[_JobRequestGroup] = []
  for rel_path in rel_paths:
    groups.extend(await _EnumerateFileJobReqGroups(directory=directory,
                                                   rel_path=rel_path,
                                                   chunk_size=chunk_size,
                                                   group_size=group_size))
  return groups


async def _HashChunk(*, dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
                     rel_path: PurePosixPath, chunk_idx: int, chunk_size: int,
                     err_ctx: _ErrorContext) -> str:

  await err_ctx.Add('directory', str(directory))
  await err_ctx.Add('rel_path', str(rel_path))
  await err_ctx.Add('chunk_idx', chunk_idx)
  await err_ctx.Add('chunk_size', chunk_size)
  await err_ctx.Add('dd_cmd', dd_cmd)
  await err_ctx.Add('hash_shell_str', hash_shell_str)

  path: anyio.Path = directory / rel_path
  await err_ctx.Add('path', str(path))

  if not await directory.exists():
    raise _FileNotFoundError(
        f'Directory not found: {directory}'
        f'\n  context:\n{textwrap.indent(_YamlDump(await err_ctx.Dump()), "    ")}'
    )
  if not await path.exists():
    raise _FileNotFoundError(
        f'File not found: {path}'
        f'\n  context:\n{textwrap.indent(_YamlDump(await err_ctx.Dump()), "    ")}'
    )
  file_size = await _FileSize(path=path)
  await err_ctx.Add('file_size', file_size)
  file_chunks = _GetNumChunks(file_size=file_size, chunk_size=chunk_size)
  await err_ctx.Add('file_chunks', file_chunks)
  if chunk_idx >= file_chunks:
    raise _ChunkNotFoundError(
        f'Chunk index {chunk_idx} is greater than the number of chunks in file'
        f'\n  context:\n{textwrap.indent(_YamlDump(await err_ctx.Dump()), "    ")}'
    )
  dd_invoc = [
      dd_cmd, f'if={path}', f'bs={chunk_size}', f'skip={chunk_idx}', 'count=1'
  ]
  dd_invoc_str = shlex.join(dd_invoc)
  shell: str = f'({dd_invoc_str}) | {hash_shell_str}'
  await err_ctx.Add('dd_invoc_str', dd_invoc_str)
  await err_ctx.Add('shell', shell)
  try:
    output: str = await _Execute(cmd=['bash', '-c', shell],
                                 cwd=directory,
                                 console=None)
  except (Exception) as e:
    if isinstance(e, _CopyCheckError):
      # Don't log this because it's a copy error, which is normal because it's not
      # finished copying.
      raise
    logger.exception(f'Failed to hash chunk: ({type(e).__name__}) {str(e)}',
                     extra={'error_context': await err_ctx.Dump()})
    raise
  await err_ctx.Add('output', output)

  hash_str, _, _ = output.partition(' ')
  hash_str = hash_str.strip()
  await err_ctx.Add('hash_str', hash_str)
  if len(hash_str) == 0:
    raise _HashParseError(
        f'Hash output is empty: {json.dumps(output)}'
        f'\n  context:\n{textwrap.indent(_YamlDump(await err_ctx.Dump()), "    ")}'
    )
  valid_hex_chars = '0123456789abcdefABCDEF'
  valid_b64_chars = string.ascii_letters + string.digits + '+/='
  await err_ctx.Add('valid_hex_chars', valid_hex_chars)
  await err_ctx.Add('valid_b64_chars', valid_b64_chars)
  bad_chars = ''.join(
      set(hash_str) - (set(valid_hex_chars) | set(valid_b64_chars)))
  await err_ctx.Add('bad_chars', bad_chars)
  if len(bad_chars) > 0:
    raise _HashParseError(
        f'Hash output contains invalid characters.'
        f'\n  context:\n{textwrap.indent(_YamlDump(await err_ctx.Dump()), "    ")}'
    )
  return hash_str


async def _ExecuteJobGroup(*, dd_cmd: str, hash_shell_str: str,
                           directory: anyio.Path, req_group: _JobRequestGroup,
                           chunk_size: int,
                           err_ctx: _ErrorContext) -> _JobResultGroup:
  res_group: _JobResultGroup = _JobResultGroup(rel_path=req_group.rel_path,
                                               chunk_idx=[],
                                               hash=[])
  chunk_idx: int
  for chunk_idx in req_group.chunk_idx:
    hash_str = await _HashChunk(dd_cmd=dd_cmd,
                                hash_shell_str=hash_shell_str,
                                directory=directory,
                                rel_path=req_group.rel_path,
                                chunk_idx=chunk_idx,
                                chunk_size=chunk_size,
                                err_ctx=err_ctx)
    res_group.chunk_idx.append(chunk_idx)
    res_group.hash.append(hash_str)
  return res_group


def _ExecuteJobGroupSync(*, dd_cmd: str, hash_shell_str: str,
                         directory: anyio.Path, req_group: _JobRequestGroup,
                         chunk_size: int,
                         err_ctx: _ErrorContext) -> _JobResultGroup:
  return asyncio.run(
      _ExecuteJobGroup(dd_cmd=dd_cmd,
                       hash_shell_str=hash_shell_str,
                       directory=directory,
                       req_group=req_group,
                       chunk_size=chunk_size,
                       err_ctx=err_ctx))


async def _HashGroupsInFuture(
    *, dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
    req_groups: List[_JobRequestGroup], chunk_size: int, max_workers: int,
    err_ctx: _ErrorContext
) -> AsyncGenerator[Tuple[_JobRequestGroup, 'Future[_JobResultGroup]'], None]:
  fut2req: Dict['Future[_JobResultGroup]', _JobRequestGroup] = {}
  futures: List['Future[_JobResultGroup]'] = []
  running: Set['Future[_JobResultGroup]'] = set()

  with ThreadPoolExecutor(max_workers=max_workers) as executor:
    for req_group in req_groups:
      fut = executor.submit(_ExecuteJobGroupSync,
                            dd_cmd=dd_cmd,
                            hash_shell_str=hash_shell_str,
                            directory=directory,
                            req_group=req_group,
                            chunk_size=chunk_size,
                            err_ctx=err_ctx)
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
                        res_group: _JobResultGroup, prev_audit: AuditFileSchema,
                        chunks: int) -> _ProgressInfo:
  progress = _ProgressInfo(results=[], chunks=chunks)
  for i in range(len(res_group.chunk_idx)):
    path_str = str(res_group.rel_path)
    if path_str not in prev_audit.files:
      raise AssertionError(
          f'path_str={path_str} not in audit.files: {json.dumps(list(prev_audit.files.keys()))}'
      )

    audit_chunks: List[str] = prev_audit.files[path_str]
    if not i < len(audit_chunks):
      raise AssertionError(
          f'Index {i} is out of range for audit_chunks: {json.dumps(audit_chunks)}'
      )
    chunk_idx: int = res_group.chunk_idx[i]
    computed_hash: str = res_group.hash[i]
    expected_hash: str = audit_chunks[chunk_idx]
    if expected_hash != computed_hash:
      progress.results.append(
          _ChunkStatus(
              type=_ResultType.FAIL_MATCH,
              message=f'Hash mismatch: {expected_hash} != {computed_hash}',
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


async def _HashGroups(
    *, dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
    req_groups: List[_JobRequestGroup], chunk_size: int, max_workers: int,
    progress: _ProgressInfo, show_progress: Optional[_FormatLiteral],
    prev_audit: Optional[AuditFileSchema], console: Console,
    err_ctx: _ErrorContext
) -> AsyncGenerator[Tuple[_JobRequestGroup, Union[_JobResultGroup,
                                                  _GeneralFailure]], None]:

  progress.results = []
  progress.chunks = sum(len(req_group.chunk_idx) for req_group in req_groups)

  req_group: _JobRequestGroup
  res_group_fut: Future['_JobResultGroup']

  last_progress_t = time.time()
  async for (req_group, res_group_fut) in _HashGroupsInFuture(
      dd_cmd=dd_cmd,
      hash_shell_str=hash_shell_str,
      directory=directory,
      req_groups=req_groups,
      chunk_size=chunk_size,
      max_workers=max_workers,
      err_ctx=err_ctx):
    try:
      res_group = res_group_fut.result()
      if prev_audit is not None:
        progress_ = _MeasureHashMatches(req_group=req_group,
                                        res_group=res_group,
                                        prev_audit=prev_audit,
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
      is_copy_error = isinstance(e, _CopyCheckError)
      if not is_copy_error:
        # Only log something if it's not a copy error, because copy errors are
        # normal.
        logger.exception(f'Failed to hash chunk: ({type(e).__name__}) {str(e)}',
                         extra={'error_context': await err_ctx.Dump()})
      for chunk_idx in req_group.chunk_idx:
        progress.results.append(
            _ChunkStatus(
                type=(_ResultType.FAIL_COPY_ERROR
                      if is_copy_error else _ResultType.FAIL_OTHER_ERROR),
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

    if show_progress is not None and show_progress != 'none':
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


def _FindIgnoreFileSync(*, special_ignorefile_name: str,
                        cwd: pathlib.Path) -> Optional[pathlib.Path]:
  """
  Search for a '.rsynccheck-ignore' file in the current working directory and its ancestors.

  Returns:
      Optional[Path]: The path to the found '.rsynccheck-ignore' file, or None
        if not found.
  """
  ignorefile = cwd / special_ignorefile_name
  if ignorefile.exists() and ignorefile.is_file():
    return ignorefile
  return None


def _ConstructIgnorePathSpecsSync(*, special_ignorefile_name: Optional[str],
                                  ignorefiles: List[TextIO],
                                  ignorelines: List[str],
                                  ignore_metas: Dict[str, List[str]],
                                  cwd: pathlib.Path) -> List[pathspec.PathSpec]:

  found_ignore_file: Optional[TextIO] = None
  try:
    if special_ignorefile_name is not None:
      found_ignore_file_path: Optional[pathlib.Path] = _FindIgnoreFileSync(
          special_ignorefile_name=special_ignorefile_name, cwd=cwd)
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
  rel_paths: List[PurePosixPath]
  failures: List[_GeneralFailure]
  path2hashes: Dict[str, List[str]]
  progress: _ProgressInfo


def _CheckFailValidateReqGroups(req_groups: List[_JobRequestGroup]):
  seen: Dict[Tuple[PurePosixPath, int], int] = {}

  for idx, req_group in enumerate(req_groups):
    for chunk_idx in req_group.chunk_idx:
      key: Tuple[PurePosixPath, int] = (req_group.rel_path, chunk_idx)
      if key in seen:
        raise AssertionError(
            f'Duplicate chunk index: {key}, seen at {seen[key]} and {idx}')
      seen[key] = idx


async def Hash(dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
               rel_paths: List[PurePosixPath],
               req_groups: List[_JobRequestGroup], chunk_size: int,
               max_workers: int, show_progress: Optional[_FormatLiteral],
               prev_audit: Optional[AuditFileSchema], console: Console,
               err_ctx: _ErrorContext) -> HashResults:
  _CheckFailValidateReqGroups(req_groups)

  progress = _ProgressInfo(
      results=[],
      chunks=sum(len(req_group.chunk_idx) for req_group in req_groups))
  results: List[Tuple[_JobRequestGroup, Union[_JobResultGroup,
                                              _GeneralFailure]]]
  results = [
      req_and_res
      async for req_and_res in _HashGroups(dd_cmd=dd_cmd,
                                           hash_shell_str=hash_shell_str,
                                           directory=directory,
                                           req_groups=req_groups,
                                           chunk_size=chunk_size,
                                           max_workers=max_workers,
                                           progress=progress,
                                           show_progress=show_progress,
                                           prev_audit=prev_audit,
                                           console=console,
                                           err_ctx=err_ctx)
  ]

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
    for rel_path_str in mismatched_files:
      failures.append(
          _GeneralFailure(
              message=
              f'File in path2hashes but not in rel_paths: {rel_path_str}',
              rel_path=PurePosixPath(rel_path_str),
              chunk_idx=None,
              req_group=None,
              exception=None))

  return HashResults(rel_paths=rel_paths,
                     failures=failures,
                     path2hashes=path2hashes,
                     progress=progress)


async def HashMain(*, dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
                   file_iter_method: _FileIterMethodLiteral,
                   audit_file_ostream: anyio.AsyncFile[str],
                   ignores: List[pathspec.PathSpec],
                   ignore_metas: Dict[str, List[str]], chunk_size: int,
                   group_size: int, max_workers: int, console: Console,
                   show_progress: Optional[_FormatLiteral],
                   err_ctx: _ErrorContext):

  paths: _PathList = await _GetPaths(directory=directory,
                                     file_iter_method=file_iter_method,
                                     ignores=ignores,
                                     console=console)
  await err_ctx.Add('directory', str(directory))
  await err_ctx.Add('file_iter_method', file_iter_method)
  paths = _SortPaths(paths=paths)
  await err_ctx.Add('rel_paths',
                    [str(rel_path) for rel_path in paths.rel_paths])

  for path in sorted(paths.rel_paths):
    console.print(f'Found: {path}', style='bold blue')

  req_groups: List[_JobRequestGroup]
  req_groups = await _EnumerateAllJobReqGroups(directory=directory,
                                               rel_paths=paths.rel_paths,
                                               chunk_size=chunk_size,
                                               group_size=group_size)
  result: HashResults
  result = await Hash(dd_cmd=dd_cmd,
                      hash_shell_str=hash_shell_str,
                      directory=directory,
                      rel_paths=paths.rel_paths,
                      req_groups=req_groups,
                      chunk_size=chunk_size,
                      max_workers=max_workers,
                      show_progress=show_progress,
                      prev_audit=None,
                      console=console,
                      err_ctx=err_ctx)
  audit = AuditFileSchema(files={},
                          chunk_size=chunk_size,
                          meta_unused=AuditFileSchema.UnusedMeta(
                              dd_cmd=dd_cmd,
                              hash_shell_str=hash_shell_str,
                              group_size=group_size,
                              directory=str(directory),
                              file_iter_method=file_iter_method,
                              ignored=list(map(str, paths.ignored)),
                              max_workers=max_workers,
                              ignore_metas=ignore_metas,
                          ))
  for path_str, hashes in result.path2hashes.items():
    audit.files[path_str] = hashes

  _CheckFailFailures(failures=result.failures, console=console)

  ##############################################################################
  # Write the audit file.
  ostream = io.StringIO()
  yaml.safe_dump(audit.model_dump(mode='json', round_trip=True),
                 ostream,
                 sort_keys=False)
  await audit_file_ostream.write(ostream.getvalue())
  ##############################################################################
  console.print('Hashing complete', style='bold green')


async def AuditMain(*, dd_cmd: str, hash_shell_str: str, directory: anyio.Path,
                    audit_yaml_str: str,
                    show_progress: Optional[_FormatLiteral],
                    output_format: _FormatLiteral, mismatch_exit: int,
                    group_size: int, max_workers: int, console: Console,
                    err_ctx: _ErrorContext):
  audit_dict: Dict[str, Any] = yaml.safe_load(audit_yaml_str)
  audit: AuditFileSchema = AuditFileSchema.model_validate(audit_dict)
  await err_ctx.Add('audit', await err_ctx.LargeToFile('audit', audit_yaml_str))
  ##############################################################################
  chunk_size: int = audit.chunk_size
  rel_paths: List[PurePosixPath] = [
      PurePosixPath(path_str) for path_str in audit.files.keys()
  ]
  await err_ctx.Add('chunk_size', chunk_size)
  await err_ctx.Add('rel_paths', [str(path) for path in rel_paths])

  ##############################################################################
  # Construct jobs for hashing.
  req_groups: List[_JobRequestGroup] = []
  for path_str, hashes in audit.files.items():
    rel_path = PurePosixPath(path_str)

    for chunk_idx in range(len(hashes)):
      # If there is no previous job group, or the previous job group is full,
      # or the previous job group is for a different file, then create a new
      # job group.
      if (len(req_groups) == 0 or len(req_groups[-1].chunk_idx) >= group_size
          or req_groups[-1].rel_path != rel_path):
        req_groups.append(_JobRequestGroup(rel_path=rel_path, chunk_idx=[]))
      # Now that we know the last job group is valid and compatible, add the
      # chunk index to it.
      req_groups[-1].chunk_idx.append(chunk_idx)
  _CheckFailValidateReqGroups(req_groups)
  await err_ctx.Add(
      'req_groups', await err_ctx.LargeToFile('req_groups', [
          req_group.model_dump(mode='json', round_trip=True)
          for req_group in req_groups
      ]))
  ##############################################################################
  # Print out some information about the files we are auditing.
  for path_str, hashes in audit.files.items():
    path: anyio.Path = directory / path_str
    found_str: str
    if not await path.exists():
      found_str = 'not found'
    else:
      found_str = 'found'
    console.print(f'Auditing: {path_str} ({found_str})', style='bold blue')
  ##############################################################################
  results: HashResults
  results = await Hash(dd_cmd=dd_cmd,
                       hash_shell_str=hash_shell_str,
                       directory=directory,
                       rel_paths=rel_paths,
                       req_groups=req_groups,
                       chunk_size=chunk_size,
                       max_workers=max_workers,
                       show_progress=show_progress,
                       prev_audit=audit,
                       console=console,
                       err_ctx=err_ctx)
  await err_ctx.Add('result.rel_paths',
                    [str(rel_path) for rel_path in results.rel_paths])
  await err_ctx.Add('result.path2hashes', results.path2hashes)
  second_audit = audit.model_copy(update={'files': {}}, deep=True)

  for path_str, hashes in results.path2hashes.items():
    second_audit.files[path_str] = hashes
  await err_ctx.Add(name='second_audit',
                    msg=await err_ctx.LargeToFile(name='second_audit',
                                                  msg=second_audit.model_dump(
                                                      mode='json',
                                                      round_trip=True)))
  ##############################################################################
  sys.stderr.flush()
  sys.stdout.flush()
  print(_Format(progress=results.progress, format=output_format))
  sys.stderr.flush()
  sys.stdout.flush()

  summary = results.progress.Summary()
  await err_ctx.Add(name='summary',
                    msg=summary.model_dump(mode='json', round_trip=True))
  ##############################################################################
  # Find the chunk indices that are different, broken down by file.
  delta_chunks: Dict[str, List[int]] = defaultdict(list)
  for path_str, hashes in audit.files.items():
    dst_hashes = second_audit.files.get(path_str, [])
    for i in range(len(hashes)):
      src_hash = hashes[i]
      dst_hash: Union[str,
                      None] = dst_hashes[i] if i < len(dst_hashes) else None
      if src_hash != dst_hash:
        delta_chunks[path_str].append(i)
  await err_ctx.Add(name='delta_chunks',
                    msg=await err_ctx.LargeToFile(name='delta_chunks',
                                                  msg=dict(delta_chunks)))
  # Summary mismatch count by file.
  delta_files: Dict[str, int] = {
      path_str: sum(file_chunks)
      for path_str, file_chunks in delta_chunks.items()
  }
  await err_ctx.Add(name='delta_files',
                    msg=await err_ctx.LargeToFile(name='delta_files',
                                                  msg=dict(delta_files)))
  ##############################################################################
  # Now there are two ways to check if the audit passes:
  # 1. compare audit.files and second_audit.files.
  # 2. compare progress.Sumarry().success with the number of chunks.

  hashes_match = audit.files == second_audit.files
  finished_with_no_errors = summary.success == results.progress.chunks
  await err_ctx.Add(name='hashes_match', msg=hashes_match)
  await err_ctx.Add(name='finished_with_no_errors', msg=finished_with_no_errors)

  if hashes_match != finished_with_no_errors:
    logger.error(
        'Internal error: Somehow, the audit passed in one way but not the other',
        extra={'error_context': await err_ctx.Dump()})
    console.print('error_context:', style='bold red')
    console.print(textwrap.indent(_YamlDump(await err_ctx.Dump()), '  '),
                  style='bold red')
    raise AssertionError(
        'Internal error: Somehow, the audit passed in one way but not the other:'
        f'\n hashes_match={hashes_match}'
        f'\n finished_with_no_errors={finished_with_no_errors}')

  if hashes_match:
    console.print('Files match!', style='bold green')
    sys.exit(0)
  console.print('Files do not match!', style='bold red')
  sys.exit(mismatch_exit)
