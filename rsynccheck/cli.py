# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
#
# The RSyncCheck project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.
"""Check the completeness of an rsync operation."""
import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
from shutil import get_terminal_size
from typing import List, NamedTuple, Optional

import anyio
import humanfriendly
from rich.console import Console
from rich_argparse import RichHelpFormatter

from . import _build_version
from .rsynccheck import (_VALID_FILE_ITER_METHODS, _VALID_FORMATS, AuditMain,
                         HashMain, _FileIterMethodLiteral, _FormatLiteral,
                         _GetPath)
from .utilities.error_utils import _ErrorContext

logger = logging.getLogger(__name__)

_DEFAULT_LOGS_ARTIFACTS_PATH = pathlib.Path('~/.rsynccheck/logs/artifacts')
_DEFAULT_FILE_ITER_METHOD: _FileIterMethodLiteral = 'iterdir'
_DEFAULT_HASH_SHELL_STR = 'xxhsum -H0'
# ChatGPT says that the maximum page size for SSD is 128KB, and the maximum
# track size for HDD is 256KB. It shouldn't hurt to be conservative here.
#
# TODO: We should compute the optimal chunk size based on disk. ChatGPT says
# fio is a good tool for this. Or we can test it using rsynccheck itself.
_DEFAULT_CHUNK_SIZE = '256KiB'
# TODO: We should compute the optimal group size based on the chunk size and
# the size of the files in the directory. Or we can test it using rsynccheck
# itself.
_DEFAULT_GROUP_SIZE = 10
# Use 0 to use the number of CPUs.
_DEFAULT_MAX_WORKERS = 0
_DEFAULT_MISMATCH_EXIT = 1


def _GetProgramName() -> str:
  if __package__:
    # Use __package__ to get the base package name
    base_module_path = __package__
    # Infer the module name from the file path, with assumptions about the structure
    module_name = pathlib.Path(__file__).stem
    # Construct what might be the intended full module path
    full_module_path = f'{base_module_path}.{module_name}' if base_module_path else module_name
    return f'python -m {full_module_path}'
  else:
    return sys.argv[0]


def _AddIgnoreArgs(parser: argparse.ArgumentParser):
  parser.add_argument('--ignorefile',
                      type=str,
                      action='append',
                      default=[],
                      help='File containing additional ignore patterns.'
                      ' Follows gitignore syntax.'
                      ' See <https://github.com/cpburnz/python-pathspec>.'
                      ' Can be used more than once.'
                      ' Path is interpreted as relative to the user\'s cwd.')
  parser.add_argument('--ignoreline',
                      type=str,
                      action='append',
                      default=[],
                      help='Ignore pattern. Follows gitignore syntax.'
                      ' See <https://github.com/cpburnz/python-pathspec>.'
                      ' Can be used more than once.')
  parser.add_argument(
      '--special-ignorefile-name',
      type=str,
      default='.rsynccheckignore',
      help=
      'Special ignorefile name. Searches for this file name in the root of the'
      ' target directory and treats it like an --ignorefile.'
      ' Default is ".rsynccheckignore". Set to "" to disable.')


def _AddDirectoryArgs(parser: argparse.ArgumentParser, *, action: str):
  parser.add_argument('--directory',
                      type=pathlib.Path,
                      required=True,
                      help=f'Directory to {action}.')


def _AddHashingArgs(parser: argparse.ArgumentParser):
  parser.add_argument(
      '--progress',
      choices=_VALID_FORMATS,
      default='yaml',
      help=
      f'Progress format to use. Default is "yaml". Choices are {_VALID_FORMATS}.'
  )

  parser.add_argument(
      '--group-size',
      type=int,
      default=_DEFAULT_GROUP_SIZE,
      help=
      f'Number of chunks to run on a single thread, in sequence. Default is {_DEFAULT_GROUP_SIZE}.'
  )
  parser.add_argument(
      '--max-workers',
      type=int,
      default=_DEFAULT_MAX_WORKERS,
      help=
      f'Maximum number of workers to use for hashing. To use the number of CPUs, set to 0. Default is {_DEFAULT_MAX_WORKERS}.'
  )
  parser.add_argument(
      '--hash-shell-str',
      type=str,
      default=_DEFAULT_HASH_SHELL_STR,
      help=
      f'Shell string/command to hash files with. Default is {json.dumps(_DEFAULT_HASH_SHELL_STR)}'
  )
  parser.add_argument('--dd-cmd',
                      type=str,
                      default='dd',
                      help='Command to split chunks with. Default is "dd".')


class _CustomRichHelpFormatter(RichHelpFormatter):

  def __init__(self, *args, **kwargs):
    if kwargs.get('width') is None:
      width, _ = get_terminal_size()
      if width == 0:
        if not os.getenv('SUPPRESS_TERMINAL_WARNING'):
          logger.warning(
              'Terminal width was set to 0, using default width of 80.')
        # This is the default in get_terminal_size().
        width = 80
      # This is what HelpFormatter does to the width returned by
      # `get_terminal_size()`.
      width -= 2
      kwargs['width'] = width
    super().__init__(*args, **kwargs)


def _GetMaxWorkers(max_workers: int) -> int:
  if max_workers == 0:
    import multiprocessing
    return multiprocessing.cpu_count()
  return max_workers


class HashingParams(NamedTuple):
  dd_cmd: str
  hash_shell_str: str
  group_size: int
  max_workers: int


def _GetHashingParams(args: argparse.Namespace) -> HashingParams:

  return HashingParams(dd_cmd=args.dd_cmd,
                       hash_shell_str=args.hash_shell_str,
                       max_workers=_GetMaxWorkers(args.max_workers),
                       group_size=args.group_size)


async def _HashMainFromArgs(args: argparse.Namespace, console: Console):
  hashing_params = _GetHashingParams(args)
  directory: anyio.Path = await _GetPath(anyio.Path(args.directory))
  file_iter_method: _FileIterMethodLiteral = args.file_iter_method
  special_ignorefile_name: str = args.special_ignorefile_name
  ignorefiles: List[str] = list(args.ignorefile)
  ignorelines: List[str] = list(args.ignoreline)
  chunk_size: int = args.chunk_size
  show_progress: Optional[_FormatLiteral] = args.progress
  logs_artifacts_path: anyio.Path = await _GetPath(
      anyio.Path(args.logs_artifacts_path))
  logs_artifacts_path = await logs_artifacts_path.resolve()
  audit_file: str = args.audit_file

  err_ctx = await _ErrorContext.Create(logs_artifacts_path=logs_artifacts_path,
                                       key='hash')
  async with err_ctx:
    return await HashMain(dd_cmd=hashing_params.dd_cmd,
                          hash_shell_str=hashing_params.hash_shell_str,
                          directory=directory,
                          file_iter_method=file_iter_method,
                          special_ignorefile_name=special_ignorefile_name,
                          ignorefiles=ignorefiles,
                          ignorelines=ignorelines,
                          audit_file=audit_file,
                          chunk_size=chunk_size,
                          group_size=hashing_params.group_size,
                          max_workers=hashing_params.max_workers,
                          show_progress=show_progress,
                          console=console,
                          err_ctx=err_ctx.StepInto())


async def _AuditMainFromArgs(args: argparse.Namespace, console: Console):
  hashing_args = _GetHashingParams(args)
  directory: anyio.Path = await _GetPath(anyio.Path(args.directory))
  show_progress: Optional[_FormatLiteral] = args.progress
  output_format: _FormatLiteral = args.output_format
  mismatch_exit: int = args.mismatch_exit
  logs_artifacts_path = await _GetPath(anyio.Path(args.logs_artifacts_path))
  audit_file: str = args.audit_file

  err_ctx = await _ErrorContext.Create(logs_artifacts_path=logs_artifacts_path,
                                       key='audit')
  async with err_ctx:
    return await AuditMain(dd_cmd=hashing_args.dd_cmd,
                           hash_shell_str=hashing_args.hash_shell_str,
                           directory=directory,
                           audit_file=audit_file,
                           group_size=hashing_args.group_size,
                           max_workers=_GetMaxWorkers(args.max_workers),
                           show_progress=show_progress,
                           output_format=output_format,
                           mismatch_exit=mismatch_exit,
                           console=console,
                           err_ctx=err_ctx.StepInto())


async def amain():
  console = Console(file=sys.stderr)
  try:
    parser = argparse.ArgumentParser(prog=_GetProgramName(),
                                     description=__doc__,
                                     formatter_class=_CustomRichHelpFormatter)

    parser.add_argument('--version', action='version', version=_build_version)
    parser.add_argument(
        '--logs-artifacts-path',
        type=pathlib.Path,
        default=_DEFAULT_LOGS_ARTIFACTS_PATH,
        help=
        f'Path to store logs and artifacts. Default is {_DEFAULT_LOGS_ARTIFACTS_PATH}.'
    )

    cmd = parser.add_subparsers(required=True, dest='cmd')
    ############################################################################
    hash_cmd_parser = cmd.add_parser('hash',
                                     help='Hash files in a directory.',
                                     description='Hash files in a directory.',
                                     formatter_class=_CustomRichHelpFormatter)
    _AddDirectoryArgs(hash_cmd_parser, action='hash')
    hash_cmd_parser.add_argument(
        '--file-iter-method',
        choices=_VALID_FILE_ITER_METHODS,
        default=_DEFAULT_FILE_ITER_METHOD,
        help=
        'Method to use to list files. git ls-files to enumerate staged files.'
        ' iterdir, iterates the file system.'
        ' auto uses git if the directory is a git repo, otherwise iterdir.'
        f' Default is {_DEFAULT_FILE_ITER_METHOD}.')
    hash_cmd_parser.add_argument(
        '--chunk-size',
        type=humanfriendly.parse_size,
        default=humanfriendly.parse_size(_DEFAULT_CHUNK_SIZE),
        help=f'Chunk size (in bytes) to use when hashing files.'
        ' Supports sizes in human-readable format.'
        ' See https://pypi.org/project/humanfriendly/ for supported formats.'
        f' Default is {json.dumps(_DEFAULT_CHUNK_SIZE)}.')

    hash_cmd_parser.add_argument(
        '--audit-file',
        type=str,
        required=True,
        help=
        'File to output the hashes to, used for auditing. Use "-" for stdout.')
    _AddIgnoreArgs(hash_cmd_parser)
    _AddHashingArgs(hash_cmd_parser)
    ############################################################################
    audit_cmd_parser = cmd.add_parser(
        'audit',
        help=
        'Audit files in a directory using an existing audit file produced by `hash` command.',
        description=
        'Audit files in a directory using an existing audit file produced by `hash` command.',
        formatter_class=_CustomRichHelpFormatter)
    _AddDirectoryArgs(audit_cmd_parser, action='audit')
    audit_cmd_parser.add_argument(
        '--audit-file',
        type=str,
        required=True,
        help='File to read hashes from, used for auditing. Use "-" for stdin.')
    audit_cmd_parser.add_argument(
        '--output-format',
        choices=_VALID_FORMATS,
        default='yaml',
        help=
        f'Output format to use. Output is written to stdout. Default is "yaml". Choices are {_VALID_FORMATS}.'
    )
    audit_cmd_parser.add_argument(
        '--mismatch-exit',
        type=int,
        default=_DEFAULT_MISMATCH_EXIT,
        help=
        f'Exit code to use if a mismatch is found. Default is {_DEFAULT_MISMATCH_EXIT}.'
    )
    _AddHashingArgs(audit_cmd_parser)
    ############################################################################

    args = parser.parse_args()

    if args.cmd == 'hash':
      await _HashMainFromArgs(args, console)
    elif args.cmd == 'audit':
      await _AuditMainFromArgs(args, console)
    else:
      raise argparse.ArgumentError(argument=None,
                                   message=f'Unknown command {args.cmd},'
                                   ' expected {hash, audit}.')
  except Exception:
    logger.exception('Unhandled exception.')
    console.print_exception()
    sys.exit(1)


def main():
  asyncio.run(amain())


if __name__ == '__main__':
  main()
