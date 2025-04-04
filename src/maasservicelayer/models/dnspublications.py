# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).
from datetime import datetime
import hashlib

from pydantic import Field

from maasservicelayer.models.base import generate_builder, MaasBaseModel
from maasservicelayer.utils.date import utcnow


@generate_builder()
class DNSPublication(MaasBaseModel):
    created: datetime = Field(default=utcnow())
    serial: int
    source: str
    update: str

    def etag(self) -> str:
        m = hashlib.sha256()
        m.update(self.created.isoformat().encode("utf-8"))
        return m.hexdigest()
