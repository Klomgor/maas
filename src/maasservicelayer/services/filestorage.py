#  Copyright 2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).
from maasservicelayer.builders.filestorage import FileStorageBuilder
from maasservicelayer.context import Context
from maasservicelayer.db.repositories.filestorage import FileStorageRepository
from maasservicelayer.models.filestorage import FileStorage
from maasservicelayer.services.base import BaseService, ServiceCache


class FileStorageService(
    BaseService[FileStorage, FileStorageRepository, FileStorageBuilder]
):
    def __init__(
        self,
        context: Context,
        repository: FileStorageRepository,
        cache: ServiceCache | None = None,
    ):
        super().__init__(context, repository, cache)

    async def create(self, builder):
        raise NotImplementedError(
            "Create is not supported for file storage. Use `create_or_update`."
        )

    async def create_or_update(
        self, builder: FileStorageBuilder
    ) -> FileStorage:
        return await self.repository.create_or_update(builder)

    async def update_by_id(self, id, builder, etag_if_match=None):
        raise NotImplementedError("Update is not supported for file storage")

    async def update_many(self, query, builder):
        raise NotImplementedError("Update is not supported for file storage")

    async def update_one(self, query, builder, etag_if_match=None):
        raise NotImplementedError("Update is not supported for file storage")

    async def _update_resource(
        self, existing_resource, builder, etag_if_match=None
    ):
        raise NotImplementedError("Update is not supported for file storage")
