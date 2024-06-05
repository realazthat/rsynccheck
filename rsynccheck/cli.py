"""Check the completeness of an rsync operation."""
import argparse
import json
import sys
import warnings
from pathlib import Path
from shutil import get_terminal_size
from typing import List, NamedTuple, Optional

import humanize
from rich.console import Console
from rich_argparse import RichHelpFormatter
from typing_extensions import Dict

from . import _build_version
from .rsynccheck import (_VALID_FILE_ITER_METHODS, _VALID_FORMATS, AuditMain,
                         HashMain, _ConstructIgnorePathSpecs,
                         _FileIterMethodLiteral, _FormatLiteral)

_DEFAULT_HASH_SHELL_STR = 'xxhsum -H0'
_DEFAULT_CHUNK_SIZE = 4096 * 1024
_DEFAULT_GROUP_SIZE = 10
_DEFAULT_MAX_WORKERS = 10
_DEFAULT_MISMATCH_EXIT = 1


def _GetProgramName() -> str:
  if __package__:
    # Use __package__ to get the base package name
    base_module_path = __package__
    # Infer the module name from the file path, with assumptions about the structure
    module_name = Path(__file__).stem
    # Construct what might be the intended full module path
    full_module_path = f'{base_module_path}.{module_name}' if base_module_path else module_name
    return f'python -m {full_module_path}'
  else:
    return sys.argv[0]


def _AddIgnoreArgs(parser: argparse.ArgumentParser):
  parser.add_argument('--ignorefile',
                      type=argparse.FileType('r'),
                      action='append',
                      default=[],
                      help='File containing additional ignore patterns.'
                      ' Follows gitignore syntax.'
                      ' See <https://github.com/cpburnz/python-pathspec>.'
                      ' Can be used more than once.')
  parser.add_argument('--ignoreline',
                      type=str,
                      action='append',
                      default=[],
                      help='Ignore pattern. Follows gitignore syntax.'
                      ' See <https://github.com/cpburnz/python-pathspec>.'
                      ' Can be used more than once.')


def _AddDirectoryArgs(parser: argparse.ArgumentParser, *, action: str):
  parser.add_argument('--directory',
                      type=Path,
                      required=True,
                      help=f'Directory to {action}.')


def _AddHashingArgs(parser: argparse.ArgumentParser):
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
                      help='Command to copy files with. Default is "dd".')
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
      '--progress',
      choices=_VALID_FORMATS,
      default='yaml',
      help=
      f'Progress format to use. Default is "yaml". Choices are {_VALID_FORMATS}.'
  )


class _CustomRichHelpFormatter(RichHelpFormatter):

  def __init__(self, *args, **kwargs):
    if kwargs.get('width') is None:
      width, _ = get_terminal_size()
      if width == 0:
        warnings.warn('Terminal width was set to 0, using default width of 80.',
                      RuntimeWarning,
                      stacklevel=0)
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


def _GetPath(path: Path) -> Path:
  return path.expanduser().resolve()


class HashingArgs(NamedTuple):
  dd_cmd: str
  hash_shell_str: str
  group_size: int
  max_workers: int


def _GetHashingArgs(args: argparse.Namespace) -> HashingArgs:

  return HashingArgs(dd_cmd=args.dd_cmd,
                     hash_shell_str=args.hash_shell_str,
                     max_workers=_GetMaxWorkers(args.max_workers),
                     group_size=args.group_size)


def main():
  console = Console(file=sys.stderr)
  try:
    parser = argparse.ArgumentParser(prog=_GetProgramName(),
                                     description=__doc__,
                                     formatter_class=_CustomRichHelpFormatter)

    parser.add_argument('--version', action='version', version=_build_version)

    cmd = parser.add_subparsers(required=True, dest='cmd')
    hash_cmd_parser = cmd.add_parser('hash', help='Hash files in a directory.')
    hash_cmd_parser.add_argument(
        '--file-iter-method',
        choices=_VALID_FILE_ITER_METHODS,
        default='iterdir',
        help=
        'Method to use to list files. git ls-files to enumerate staged files. iterdir, iterates the file system. auto uses git if the directory is a git repo, otherwise iterdir.'
    )
    _AddIgnoreArgs(hash_cmd_parser)
    _AddDirectoryArgs(hash_cmd_parser, action='hash')
    _AddHashingArgs(hash_cmd_parser)
    hash_cmd_parser.add_argument(
        '--chunk-size',
        type=int,
        default=_DEFAULT_CHUNK_SIZE,
        help=
        f'Chunk size to use when hashing files. Default is {humanize.naturalsize(_DEFAULT_CHUNK_SIZE)}.'
    )
    hash_cmd_parser.add_argument(
        '--audit-file',
        type=Path,
        required=True,
        help='File to output the hashes to, used for auditing.')
    audit_cmd_parser = cmd.add_parser(
        'audit',
        help=
        'Audit files in a directory using an existing audit file produced by `hash` command.'
    )
    _AddDirectoryArgs(audit_cmd_parser, action='audit')
    audit_cmd_parser.add_argument(
        '--audit-file',
        type=Path,
        required=True,
        help='File to read hashes from, used for auditing.')
    _AddHashingArgs(audit_cmd_parser)
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

    args = parser.parse_args()

    if args.cmd == 'hash':
      hashing_args = _GetHashingArgs(args)
      ignore_metas: Dict[str, List[str]] = {}
      ignores = _ConstructIgnorePathSpecs(ignorefiles=list(args.ignorefile),
                                          ignorelines=list(args.ignoreline),
                                          ignore_metas=ignore_metas,
                                          cwd=args.directory)
      file_iter_method: _FileIterMethodLiteral = args.file_iter_method
      chunk_size: int = args.chunk_size
      show_progress: Optional[_FormatLiteral] = args.progress
      return HashMain(dd_cmd=hashing_args.dd_cmd,
                      hash_shell_str=hashing_args.hash_shell_str,
                      directory=_GetPath(args.directory),
                      file_iter_method=file_iter_method,
                      audit_file_path=_GetPath(args.audit_file),
                      ignores=ignores,
                      ignore_metas=ignore_metas,
                      chunk_size=chunk_size,
                      group_size=hashing_args.group_size,
                      max_workers=hashing_args.max_workers,
                      show_progress=show_progress,
                      console=console)

    elif args.cmd == 'audit':
      hashing_args = _GetHashingArgs(args)
      show_progress: Optional[_FormatLiteral] = args.progress
      output_format: _FormatLiteral = args.output_format
      mismatch_exit: int = args.mismatch_exit
      return AuditMain(dd_cmd=hashing_args.dd_cmd,
                       hash_shell_str=hashing_args.hash_shell_str,
                       directory=_GetPath(args.directory),
                       audit_file_path=_GetPath(args.audit_file),
                       group_size=hashing_args.group_size,
                       max_workers=_GetMaxWorkers(args.max_workers),
                       show_progress=show_progress,
                       output_format=output_format,
                       mismatch_exit=mismatch_exit,
                       console=console)
    else:
      raise argparse.ArgumentError(argument=None,
                                   message=f'Unknown command {args.cmd},'
                                   ' expected {hash, audit}.')
  except Exception:
    console.print_exception()
    sys.exit(1)


if __name__ == '__main__':
  main()
