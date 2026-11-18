# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Quantities written as an integer or a number with a decimal suffix (k, M, G)."""

from typing import Any, Optional

SCALE_SUFFIX = {"k": 10**3, "M": 10**6, "G": 10**9}


def parse_scaled(value: Any) -> Optional[int]:
    """Integer of a quantity such as 1500, 500k or 2.4M; None for None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    scale = SCALE_SUFFIX.get(text[-1])
    return int(float(text[:-1]) * scale) if scale else int(float(text))
