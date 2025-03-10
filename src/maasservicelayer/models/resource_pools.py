# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from maasservicelayer.models.base import (
    generate_builder,
    MaasTimestampedBaseModel,
)


@generate_builder()
class ResourcePool(MaasTimestampedBaseModel):
    name: str
    description: str

    def is_default(self) -> bool:
        return self.id == 0


class ResourcePoolWithSummary(ResourcePool):
    machine_total_count: int
    machine_ready_count: int
