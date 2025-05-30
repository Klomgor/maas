# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from pydantic import IPvAnyAddress

from maasservicelayer.models.base import (
    generate_builder,
    MaasTimestampedBaseModel,
)


@generate_builder()
class StaticRoute(MaasTimestampedBaseModel):
    gateway_ip: IPvAnyAddress
    metric: int
    destination_id: int
    source_id: int
