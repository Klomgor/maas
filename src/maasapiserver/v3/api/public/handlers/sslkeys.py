#  Copyright 2025 Canonical Ltd.  This software is licensed under the
#  GNU Affero General Public License version 3 (see the file LICENSE).

from typing import Sequence, Union

from fastapi import Depends, Header, Response
from starlette import status
from starlette.concurrency import run_in_threadpool

from maasapiserver.common.api.base import Handler, handler
from maasapiserver.common.api.models.responses.errors import (
    ConflictBodyResponse,
    NotFoundBodyResponse,
    UnauthorizedBodyResponse,
)
from maasapiserver.v3.api import services
from maasapiserver.v3.api.public.models.requests.query import PaginationParams
from maasapiserver.v3.api.public.models.requests.sslkeys import SSLKeyRequest
from maasapiserver.v3.api.public.models.responses.base import (
    OPENAPI_ETAG_HEADER,
)
from maasapiserver.v3.api.public.models.responses.sslkey import (
    SSLKeyListResponse,
    SSLKeyResponse,
    SSLKeysWithSummaryListResponse,
    SSLKeyWithSummaryResponse,
)
from maasapiserver.v3.auth.base import (
    check_permissions,
    get_authenticated_user,
)
from maasapiserver.v3.constants import V3_API_PREFIX
from maasservicelayer.auth.jwt import UserRole
from maasservicelayer.db.filters import QuerySpec
from maasservicelayer.db.repositories.sslkeys import SSLKeyClauseFactory
from maasservicelayer.exceptions.catalog import NotFoundException
from maasservicelayer.models.auth import AuthenticatedUser
from maasservicelayer.models.sslkeys import SSLKey
from maasservicelayer.services import ServiceCollectionV3


class SSLKeysHandler(Handler):
    """SSL Key Handler"""

    TAGS = ["SSLKeys"]

    @handler(
        path="/users/me/sslkeys",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": SSLKeyListResponse,
            },
            401: {"model": UnauthorizedBodyResponse},
        },
        response_model_exclude_none=True,
        status_code=200,
        dependencies=[
            Depends(check_permissions(required_roles={UserRole.USER}))
        ],
    )
    async def get_user_sslkeys(
        self,
        authenticated_user: AuthenticatedUser | None = Depends(  # noqa: B008
            get_authenticated_user
        ),
        pagination_params: PaginationParams = Depends(),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> SSLKeyListResponse:
        assert authenticated_user is not None

        sslkeys = await services.sslkeys.list(
            page=pagination_params.page,
            size=pagination_params.size,
            query=QuerySpec(
                where=SSLKeyClauseFactory.with_user_id(authenticated_user.id),
            ),
        )

        return SSLKeyListResponse(
            items=[
                SSLKeyResponse.from_model(
                    sslkey=sslkey,
                )
                for sslkey in sslkeys.items
            ],
            total=sslkeys.total,
            next=(
                f"{V3_API_PREFIX}/users/me/sslkeys?"
                f"{pagination_params.to_next_href_format()}"
                if sslkeys.has_next(
                    pagination_params.page, pagination_params.size
                )
                else None
            ),
        )

    @handler(
        path="/users/me/sslkeys_with_summary",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": SSLKeysWithSummaryListResponse,
            },
            401: {"model": UnauthorizedBodyResponse},
        },
        summary="List sslkeys with a summary. ONLY FOR INTERNAL USAGE.",
        description="List sslkeys with a summary. This endpoint is only for internal usage and might be changed or removed "
        "without notice.",
        response_model_exclude_none=True,
        status_code=200,
        dependencies=[
            Depends(check_permissions(required_roles={UserRole.USER}))
        ],
    )
    async def get_user_sslkeys_with_summary(
        self,
        authenticated_user: AuthenticatedUser | None = Depends(  # noqa: B008
            get_authenticated_user
        ),
        pagination_params: PaginationParams = Depends(),  # noqa: B008
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> SSLKeysWithSummaryListResponse:
        assert authenticated_user is not None

        sslkeys = await services.sslkeys.list(
            page=pagination_params.page,
            size=pagination_params.size,
            query=QuerySpec(
                where=SSLKeyClauseFactory.with_user_id(authenticated_user.id),
            ),
        )

        def _build_items(items: Sequence[SSLKey]):
            return [
                SSLKeyWithSummaryResponse.from_model(
                    sslkey=sslkey,
                )
                for sslkey in items
            ]

        items = await run_in_threadpool(_build_items, sslkeys.items)
        return SSLKeysWithSummaryListResponse(
            items=items,
            total=sslkeys.total,
            next=(
                f"{V3_API_PREFIX}/users/me/sslkeys_with_summary?"
                f"{pagination_params.to_next_href_format()}"
                if sslkeys.has_next(
                    pagination_params.page, pagination_params.size
                )
                else None
            ),
        )

    @handler(
        path="/users/me/sslkeys/{sslkey_id}",
        methods=["GET"],
        tags=TAGS,
        responses={
            200: {
                "model": SSLKeyResponse,
                "headers": {
                    "ETag": {"description": "The ETag for the resource"}
                },
            },
            404: {"model": NotFoundBodyResponse},
        },
        response_model_exclude_none=True,
        status_code=200,
        dependencies=[
            Depends(check_permissions(required_roles={UserRole.USER}))
        ],
    )
    async def get_user_sslkey(
        self,
        sslkey_id: int,
        response: Response,
        authenticated_user: AuthenticatedUser | None = Depends(  # noqa: B008
            get_authenticated_user
        ),
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> SSLKeyResponse:
        assert authenticated_user is not None

        sslkey = await services.sslkeys.get_one(
            query=QuerySpec(
                where=SSLKeyClauseFactory.and_clauses(
                    [
                        SSLKeyClauseFactory.with_id(sslkey_id),
                        SSLKeyClauseFactory.with_user_id(
                            authenticated_user.id
                        ),
                    ]
                )
            ),
        )
        if not sslkey:
            raise NotFoundException()

        response.headers["ETag"] = sslkey.etag()
        return SSLKeyResponse.from_model(
            sslkey=sslkey,
        )

    @handler(
        path="/users/me/sslkeys",
        methods=["POST"],
        tags=TAGS,
        responses={
            201: {
                "model": SSLKeyResponse,
                "headers": {"ETag": OPENAPI_ETAG_HEADER},
            },
            409: {"model": ConflictBodyResponse},
        },
        status_code=201,
        response_model_exclude_none=True,
        dependencies=[
            Depends(check_permissions(required_roles={UserRole.USER}))
        ],
    )
    async def create_user_sslkey(
        self,
        sslkey_request: SSLKeyRequest,
        response: Response,
        authenticated_user: AuthenticatedUser | None = Depends(  # noqa: B008
            get_authenticated_user
        ),
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> SSLKeyResponse:
        assert authenticated_user is not None

        builder = sslkey_request.to_builder()
        builder.user_id = authenticated_user.id
        new_sslkey = await services.sslkeys.create(builder)

        response.headers["ETag"] = new_sslkey.etag()
        return SSLKeyResponse.from_model(sslkey=new_sslkey)

    @handler(
        path="/users/me/sslkeys/{sslkey_id}",
        methods=["DELETE"],
        tags=TAGS,
        responses={
            204: {},
            404: {"model": NotFoundBodyResponse},
        },
        status_code=204,
        dependencies=[
            Depends(check_permissions(required_roles={UserRole.USER}))
        ],
    )
    async def delete_user_sslkey(
        self,
        sslkey_id: int,
        authenticated_user: AuthenticatedUser | None = Depends(  # noqa: B008
            get_authenticated_user
        ),
        etag_if_match: Union[str, None] = Header(
            alias="if-match", default=None
        ),
        services: ServiceCollectionV3 = Depends(services),  # noqa: B008
    ) -> Response:
        assert authenticated_user is not None

        await services.sslkeys.delete_one(
            query=QuerySpec(
                where=SSLKeyClauseFactory.and_clauses(
                    [
                        SSLKeyClauseFactory.with_id(sslkey_id),
                        SSLKeyClauseFactory.with_user_id(
                            authenticated_user.id
                        ),
                    ]
                )
            ),
            etag_if_match=etag_if_match,
        )

        return Response(status_code=status.HTTP_204_NO_CONTENT)
