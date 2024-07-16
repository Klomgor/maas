#  Copyright 2024 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from typing import Optional

from pydantic import BaseModel


class UserDetailsResponse(BaseModel):
    username: str
    fullname: Optional[str]
    email: Optional[str]
