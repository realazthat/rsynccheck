# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
#
# The RSyncCheck project requires contributions made to this file be licensed
# under the MIT license or a compatible open source license. See LICENSE.md for
# the license text.

from typing import Any

from typing_extensions import Annotated

JSON_SERIALIZABLE_TYPES = (str, int, float, bool, type(None), dict, list, tuple)


def IsJSONSerializable(value: Any) -> bool:
  if isinstance(value, dict):
    for k, v in value.items():
      if not IsJSONSerializable(k) or not IsJSONSerializable(v):
        return False
    return True
  if isinstance(value, (list, tuple)):
    for v in value:
      if not IsJSONSerializable(v):
        return False
    return True
  return isinstance(value, JSON_SERIALIZABLE_TYPES)


JSONSerializable = Annotated[Any, 'JSONSerializable', IsJSONSerializable]
