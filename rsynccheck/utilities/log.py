# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
#
# The RSyncCheck project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.

# SPDX-License-Identifier: MIT
#
# The ComfyLowda project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.

import json
import logging
import pathlib
import sys
import traceback
from abc import ABC, abstractmethod
from typing import List, Optional

import anyio
from pydantic import BaseModel
from typing_extensions import Literal

from .error_utils import _YamlDump


class _CustomJSONEncoder(json.JSONEncoder):

  def default(self, o):
    try:
      return super().default(o)
    except TypeError:
      return str(o)


class _CustomFormatter(logging.Formatter, ABC):

  def format_exception(self, exc_info):
    return ''.join(traceback.format_exception(*exc_info))

  @abstractmethod
  def DumpRecordDict(self, record_dict: dict) -> str:
    raise NotImplementedError()

  def format(self, record):
    record_dict = {}
    record_dict['message'] = record.getMessage()

    record_dict.update(record.__dict__)
    # record.message = record.getMessage()
    # if self.usesTime():
    #   record.asctime = self.formatTime(record, self.datefmt)
    if self.usesTime():
      record_dict['asctime'] = self.formatTime(record, self.datefmt)
    record_dict.pop('exc_info', None)
    record_dict.pop('args', None)
    record_dict.pop('msg', None)

    if record.exc_info is not None:
      record_dict['exc_info'] = self.format_exception(record.exc_info)
      record_dict['exc_text'] = super().formatException(record.exc_info)

    return self.DumpRecordDict(record_dict)


class _YAMLFormatter(_CustomFormatter):

  def DumpRecordDict(self, record_dict: dict) -> str:
    return '---\n' + _YamlDump(record_dict)


class _JSONFormatter(_CustomFormatter):

  def DumpRecordDict(self, record_dict: dict) -> str:
    return json.dumps(record_dict, cls=_CustomJSONEncoder)


class LogSettings(BaseModel):
  type: Literal['json', 'yaml', 'default']
  level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
  path: Optional[pathlib.Path]
  to_stderr: bool = False


async def _SetupLogging(*, logs: List[LogSettings]) -> None:

  handlers: List[logging.Handler] = []

  for log in logs:
    log_path: Optional[anyio.Path] = None
    this_log_handlers: List[logging.Handler] = []
    if log.path is not None:
      log_path = await anyio.Path(log.path).absolute()
    if log_path is not None:
      await log_path.parent.mkdir(parents=True, exist_ok=True)
      this_log_handlers.append(logging.FileHandler(str(log_path)))
    if log.to_stderr:
      this_log_handlers.append(logging.StreamHandler(sys.stderr))

    # Set the log level
    for handler in this_log_handlers:
      handler.setLevel(log.level)

    for handler in this_log_handlers:
      if log.type == 'json':
        handler.setFormatter(_JSONFormatter())
      elif log.type == 'yaml':
        handler.setFormatter(_YAMLFormatter())
    for handler in this_log_handlers:
      handlers.append(handler)

  logging.basicConfig(handlers=handlers)
  logging.captureWarnings(True)

  # Make sure all loggers goes to our handlers

  # for name in logging.root.manager.loggerDict:
  #   logger = logging.getLogger(name)
  #   logger.handlers = handlers
  #   logger.propagate = False
  #   logger.setLevel(log_level)
  #   logger.disabled = False
