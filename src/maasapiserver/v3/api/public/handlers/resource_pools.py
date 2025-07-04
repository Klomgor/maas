# Copyright 2024-2025 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from typing import Union

from fastapi import Depends, Header, Response
from starlette import status

from maasapiserver.common.api.base import Handler, handler
from maasapiserver.common.api.models.responses.errors import (
    BadRequestBodyResponse,
    ConflictBodyResponse,
    NotFoundBodyResponse,
)
from maasapiserver.v3.api import services
from maasapiserver.v3.api.public.models.requests.query import PaginationParams
from maasapiserver.v3.api.public.models.requests.resource_pools import (
    ResourcePoolRequest,
)
from maasapiserver.v3.api.public.models.responses.base import (
    OPENAPI_ETAG_HEADER,
)
from maasapiserver.v3.api.public.models.responses.resource_pools import (
    ResourcePoolPermission,
    ResourcePoolResponse,
    ResourcePoolsListResponse,
    ResourcePoolsWithSummaryListResponse,
    ResourcePoolWithSummaryResponse,
)
from maasapiserver.v3.auth.base import (
    check_permissions,
    get_authenticated_user,
)
from maasapiserver.v3.constants import V3_API_PREFIX
from maasservicelayer.auth.jwt import UserRole
from maasservicelayer.db.filters import QuerySpec
from maasservicelayer.db.repositories.resource_pools import (
    ResourcePoolClauseFactory,
)
from maasservicelayer.enums.rbac import RbacPermission
from maasservicelayer.exceptions.catalog import (
    BaseExceptionDetail,
    ForbiddenException,
    NotFoundException,
)
from maasservicelayer.exceptions.constants import (
    MISSING_PERMISSIONS_VIOLATION_TYPE,
)
from maasservicelayer.services import ServiceCollectionV3


class ResourcePoolHandler(Handler):
    """ResourcePool API handler."""

    TAGS = ["ResourcePool"]

    @handler(
        path="/resource_pools",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": ResourcePoolsListResponse,
            },
        },
        status_code=200,
        response_model_exclude_none=True,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.USER},
                    rbac_permissions={
                        RbacPermission.VIEW,
                        RbacPermission.VIEW_ALL,
                    },
                )
            )
        ],
    )
    async def list_resource_pools(
        self,
        pagination_params: PaginationParams = Depends(),  # noqa: B008
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> ResourcePoolsListResponse:
        query = None
        if authenticated_user.rbac_permissions:
            query = QuerySpec(
                where=ResourcePoolClauseFactory.with_ids(
                    ids=(
                        authenticated_user.rbac_permissions.visible_pools
                        | authenticated_user.rbac_permissions.view_all_pools
                    )
                )
            )
        resource_pools = await services.resource_pools.list(
            page=pagination_params.page,
            size=pagination_params.size,
            query=query,
        )
        return ResourcePoolsListResponse(
            items=[
                ResourcePoolResponse.from_model(
                    resource_pool=resource_pools,
                    self_base_hyperlink=f"{V3_API_PREFIX}/resource_pools",
                )
                for resource_pools in resource_pools.items
            ],
            total=resource_pools.total,
            next=(
                f"{V3_API_PREFIX}/resource_pools?"
                f"{pagination_params.to_next_href_format()}"
                if resource_pools.has_next(
                    pagination_params.page, pagination_params.size
                )
                else None
            ),
        )

    @handler(
        path="/resource_pools_with_summary",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": ResourcePoolsWithSummaryListResponse,
            },
        },
        summary="List resource pools with a summary. ONLY FOR INTERNAL USAGE.",
        description="List resource pools with a summary. This endpoint is only for internal usage and might be changed or removed without notice.",
        status_code=200,
        response_model_exclude_none=True,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.USER},
                    rbac_permissions={
                        RbacPermission.VIEW,
                        RbacPermission.VIEW_ALL,
                        RbacPermission.EDIT,
                    },
                )
            )
        ],
    )
    async def list_resource_pools_with_summary(
        self,
        pagination_params: PaginationParams = Depends(),  # noqa: B008
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> ResourcePoolsWithSummaryListResponse:
        query = None
        if authenticated_user.rbac_permissions:
            query = QuerySpec(
                where=ResourcePoolClauseFactory.with_ids(
                    ids=(
                        authenticated_user.rbac_permissions.visible_pools
                        | authenticated_user.rbac_permissions.view_all_pools
                    )
                )
            )
        resource_pools = await services.resource_pools.list_with_summary(
            page=pagination_params.page,
            size=pagination_params.size,
            query=query,
        )

        resource_pools_with_summary_list_response_items = []
        for resource_pool_with_summary in resource_pools.items:
            permissions = set()
            if authenticated_user.rbac_permissions:
                if authenticated_user.rbac_permissions.can_edit_all_resource_pools:
                    permissions = {
                        ResourcePoolPermission.DELETE,
                        ResourcePoolPermission.EDIT,
                    }
                elif (
                    resource_pool_with_summary.id
                    in authenticated_user.rbac_permissions.edit_pools
                ):
                    permissions = {ResourcePoolPermission.EDIT}
            else:
                if authenticated_user.is_admin():
                    permissions = {
                        ResourcePoolPermission.DELETE,
                        ResourcePoolPermission.EDIT,
                    }

            resource_pools_with_summary_list_response_items.append(
                ResourcePoolWithSummaryResponse.from_model_with_summary(
                    resource_pool_with_summary=resource_pool_with_summary,
                    permissions=permissions,
                    self_base_hyperlink=f"{V3_API_PREFIX}/resource_pools",
                )
            )

        return ResourcePoolsWithSummaryListResponse(
            items=resource_pools_with_summary_list_response_items,
            total=resource_pools.total,
            next=(
                f"{V3_API_PREFIX}/resource_pools_with_summary?"
                f"{pagination_params.to_next_href_format()}"
                if resource_pools.has_next(
                    pagination_params.page, pagination_params.size
                )
                else None
            ),
        )

    @handler(
        path="/resource_pools",
        methods=["POST"],
        tags=TAGS,
        responses={
            201: {
                "model": ResourcePoolResponse,
                "headers": {"ETag": OPENAPI_ETAG_HEADER},
            },
            409: {"model": ConflictBodyResponse},
        },
        status_code=201,
        response_model_exclude_none=True,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.ADMIN},
                    rbac_permissions={
                        RbacPermission.EDIT,
                    },
                )
            )
        ],
    )
    async def create_resource_pool(
        self,
        response: Response,
        resource_pool_request: ResourcePoolRequest,
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> ResourcePoolResponse:
        if (
            authenticated_user.rbac_permissions
            and not authenticated_user.rbac_permissions.can_edit_all_resource_pools
        ):
            raise ForbiddenException(
                details=[
                    BaseExceptionDetail(
                        type=MISSING_PERMISSIONS_VIOLATION_TYPE,
                        message="The user does not have the permissions to access this endpoint.",
                    )
                ]
            )
        resource_pool = await services.resource_pools.create(
            resource_pool_request.to_builder()
        )
        response.headers["ETag"] = resource_pool.etag()
        return ResourcePoolResponse.from_model(
            resource_pool=resource_pool,
            self_base_hyperlink=f"{V3_API_PREFIX}/resource_pools",
        )

    @handler(
        path="/resource_pools/{resource_pool_id}",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": ResourcePoolResponse,
                "headers": {"ETag": OPENAPI_ETAG_HEADER},
            },
            404: {"model": NotFoundBodyResponse},
        },
        status_code=200,
        response_model_exclude_none=True,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.USER},
                    rbac_permissions={
                        RbacPermission.VIEW,
                    },
                )
            )
        ],
    )
    async def get_resource_pool(
        self,
        resource_pool_id: int,
        response: Response,
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> ResourcePoolResponse:
        if (
            authenticated_user.rbac_permissions
            and resource_pool_id
            not in authenticated_user.rbac_permissions.visible_pools
        ):
            raise ForbiddenException(
                details=[
                    BaseExceptionDetail(
                        type=MISSING_PERMISSIONS_VIOLATION_TYPE,
                        message="The user does not have the permissions to view this resource pool.",
                    )
                ]
            )
        if resource_pool := await services.resource_pools.get_by_id(
            resource_pool_id
        ):
            response.headers["ETag"] = resource_pool.etag()
            return ResourcePoolResponse.from_model(
                resource_pool=resource_pool,
                self_base_hyperlink=f"{V3_API_PREFIX}/resource_pools",
            )
        raise NotFoundException()

    @handler(
        path="/resource_pools/{resource_pool_id}",
        methods=["PUT"],
        tags=TAGS,
        responses={
            200: {
                "model": ResourcePoolResponse,
                "headers": {"ETag": OPENAPI_ETAG_HEADER},
            },
            404: {"model": NotFoundBodyResponse},
        },
        status_code=200,
        response_model_exclude_none=True,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.ADMIN},
                    rbac_permissions={
                        RbacPermission.EDIT,
                    },
                )
            )
        ],
    )
    async def update_resource_pool(
        self,
        resource_pool_id: int,
        response: Response,
        resource_pool_request: ResourcePoolRequest,
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> ResourcePoolResponse:
        if (
            authenticated_user.rbac_permissions
            and resource_pool_id
            not in authenticated_user.rbac_permissions.edit_pools
        ):
            raise ForbiddenException(
                details=[
                    BaseExceptionDetail(
                        type=MISSING_PERMISSIONS_VIOLATION_TYPE,
                        message="The user does not have the permissions to edit this resource pool.",
                    )
                ]
            )
        resource_pool = await services.resource_pools.update_by_id(
            resource_pool_id, resource_pool_request.to_builder()
        )
        response.headers["ETag"] = resource_pool.etag()
        return ResourcePoolResponse.from_model(
            resource_pool=resource_pool,
            self_base_hyperlink=f"{V3_API_PREFIX}/resource_pools",
        )

    @handler(
        path="/resource_pools/{resource_pool_id}",
        methods=["DELETE"],
        tags=TAGS,
        responses={
            204: {},
            400: {"model": BadRequestBodyResponse},
            404: {"model": NotFoundBodyResponse},
        },
        status_code=204,
        dependencies=[
            Depends(
                check_permissions(
                    required_roles={UserRole.ADMIN},
                    rbac_permissions={RbacPermission.EDIT},
                )
            )
        ],
    )
    async def delete_resource_pool(
        self,
        resource_pool_id: int,
        etag_if_match: Union[str, None] = Header(
            alias="if-match", default=None
        ),
        authenticated_user=Depends(get_authenticated_user),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> Response:
        if (
            authenticated_user.rbac_permissions
            and resource_pool_id
            not in authenticated_user.rbac_permissions.edit_pools
        ):
            raise ForbiddenException(
                details=[
                    BaseExceptionDetail(
                        type=MISSING_PERMISSIONS_VIOLATION_TYPE,
                        message="The user does not have the permissions to edit this resource pool.",
                    )
                ]
            )
        await services.resource_pools.delete_by_id(
            resource_pool_id, etag_if_match
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
