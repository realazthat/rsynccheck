# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
#
# The RSyncCheck project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.

import copy
import io
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Union

# import yaml
import anyio
from ruamel.yaml import YAML
from slugify import slugify

from .base_types import IsJSONSerializable, JSONSerializable

logger = logging.getLogger(__name__)

# class _CustomDumper(yaml.SafeDumper):

#   def represent_tuple(self, data):
#     return self.represent_list(data)

#   def represent_object(self, data):
#     return self.represent_str(str(data))

#   def literal_presenter(self, data):
#     if '\n' in data:
#       return self.represent_scalar('tag:yaml.org,2002:str', data, style='|')
#     return self.represent_scalar('tag:yaml.org,2002:str', data)

#   def represent_unserializable(self, data):
#     return self.represent_str(f'unserializable: {str(data)}')

# _CustomDumper.add_representer(tuple, _CustomDumper.represent_tuple)
# _CustomDumper.add_representer(object, _CustomDumper.represent_object)
# _CustomDumper.add_representer(str, _CustomDumper.literal_presenter)
# _CustomDumper.add_multi_representer(object,
#                                     _CustomDumper.represent_unserializable)

# def _YamlDump(data: Any) -> str:
#   return yaml.dump(data,
#                    indent=2,
#                    Dumper=_CustomDumper,
#                    sort_keys=False,
#                    allow_unicode=True,
#                    default_flow_style=False,
#                    width=80)


def _YamlDump(data: Any) -> str:

  def custom_string_representer(dumper, data):
    style = '|' if '\n' in data else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

  yaml = YAML()
  yaml.indent(mapping=2, sequence=4, offset=2)
  yaml.width = 80
  yaml.default_flow_style = False
  yaml.allow_unicode = True
  # yaml.default_style = '|' # type: ignore
  # yaml.representer.add_representer(tuple, yaml.representer.represent_list)
  yaml.representer.add_representer(str, custom_string_representer)
  stream = io.StringIO()
  yaml.dump(data, stream)
  return stream.getvalue()


class _LargeFileReference:
  """Used for internal purposes only, for tracking large files by _ErrorContext."""

  def __init__(self, file_uri: str):
    self.file_uri = file_uri


class _ErrorContext:

  @classmethod
  async def Create(cls, *, logs_artifacts_path: Optional[anyio.Path],
                   key: str) -> '_ErrorContext':
    if logs_artifacts_path is not None:
      logs_artifacts_path = await logs_artifacts_path.absolute()
    return cls(debug_path=logs_artifacts_path,
               key=key,
               _private_use_create_instead=cls._PrivateUseCreateInstead())

  class _PrivateUseCreateInstead:
    pass

  def __init__(self, *, debug_path: Optional[anyio.Path], key: str,
               _private_use_create_instead: '_PrivateUseCreateInstead'):
    self._debug_path = debug_path
    self._key = key
    self._error_context_path: Optional[anyio.Path] = None
    if self._debug_path is not None:
      self._error_context_path = self._debug_path / slugify(self._key)
    self._context: Dict[str, Union[JSONSerializable, _LargeFileReference]] = {}

  async def LargeToFile(self, name: str,
                        msg: JSONSerializable) -> _LargeFileReference:
    if self._error_context_path is None:
      return _LargeFileReference(
          file_uri='file:///dev/null/you-did-not-set-debug-path')
    directory: anyio.Path = self._error_context_path / slugify(
        datetime.now().isoformat())
    await directory.mkdir(parents=True, exist_ok=True)
    path = directory / slugify(name)
    yaml_path = path.with_suffix('.yaml')
    await yaml_path.write_text(_YamlDump(msg))
    if not (yaml_path.is_absolute()):
      yaml_path = await yaml_path.absolute()
    return _LargeFileReference(file_uri=yaml_path.as_uri())

  async def Add(self, name: str, msg: JSONSerializable) -> None:
    if not isinstance(msg, _LargeFileReference) and not IsJSONSerializable(msg):
      raise ValueError(
          f'Value for {name} is not JSON serializable.; type(msg): {type(msg)}')
    self._context[name] = msg

  def Dump(self) -> JSONSerializable:
    # TODO: Mark the large files to have a longer TTL if it is being dumped.
    dumped = self._context.copy()
    for k, v in dumped.items():
      if isinstance(v, _LargeFileReference):
        dumped[k] = v.file_uri
    return dumped

  def UserDump(self) -> JSONSerializable:
    """Same as Dump() but avoids information that is marked as internal-only, and dereferences large files."""
    # TODO: Add the ability to mark internal-only keys.
    # TODO: dereferences large files.
    dumped = self._context.copy()
    for k, v in dumped.items():
      if isinstance(v, _LargeFileReference):
        dumped[k] = v.file_uri
    return dumped

  def Fork(self) -> '_ErrorContext':
    copy_cxt = _ErrorContext(
        debug_path=self._debug_path,
        key=self._key,
        _private_use_create_instead=self._PrivateUseCreateInstead())
    copy_cxt._context = copy.deepcopy(self._context)
    return copy_cxt

  def StepInto(self) -> '_ErrorContext':
    copy_cxt = _ErrorContext(
        debug_path=self._debug_path,
        key=self._key,
        _private_use_create_instead=self._PrivateUseCreateInstead())
    copy_cxt._context = self._context.copy()
    return copy_cxt

  def __setitem__(self, key: str, value: JSONSerializable) -> None:
    self._context[key] = value

  async def __aenter__(self) -> '_ErrorContext':
    return self.StepInto()

  async def __aexit__(self, exc_type, exc_value, traceback):
    if exc_type is not None:
      if isinstance(exc_value, BaseException):
        return None

      logger.exception(f'Error in error context: {exc_type}',
                       extra={'error_context': self.Dump()})
      return None
    return None
