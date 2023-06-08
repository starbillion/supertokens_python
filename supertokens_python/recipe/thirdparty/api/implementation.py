# Copyright (c) 2021, VRAI Labs and/or its affiliates. All rights reserved.
#
# This software is licensed under the Apache License, Version 2.0 (the
# "License") as published by the Apache Software Foundation.
#
# You may not use this file except in compliance with the License. You may
# obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Union
from urllib.parse import urlencode

from httpx import AsyncClient
from supertokens_python.exceptions import raise_general_exception
from supertokens_python.recipe.session.asyncio import create_new_session
from supertokens_python.recipe.thirdparty.interfaces import (
    APIInterface, AuthorisationUrlGetOkResponse,
    SignInUpPostFieldErrorResponse, SignInUpPostNoEmailGivenByProviderResponse,
    SignInUpPostOkResponse)
from supertokens_python.recipe.thirdparty.types import UserInfo

if TYPE_CHECKING:
    from supertokens_python.recipe.thirdparty.interfaces import (
        APIOptions, AuthorisationUrlGetResponse, SignInUpPostResponse)
    from supertokens_python.recipe.thirdparty.provider import Provider

DEV_OAUTH_CLIENT_IDS = [
    '1060725074195-kmeum4crr01uirfl2op9kd5acmi9jutn.apps.googleusercontent.com',
    # google client id
    '467101b197249757c71f'  # github client id
]
DEV_KEY_IDENTIFIER = "4398792-"
DEV_OAUTH_AUTHORIZATION_URL = 'https://supertokens.io/dev/oauth/redirect-to-provider'
DEV_OAUTH_REDIRECT_URL = 'https://supertokens.io/dev/oauth/redirect-to-app'


def is_using_oauth_development_client_id(client_id: str):
    return client_id.startswith(
        DEV_KEY_IDENTIFIER) or client_id in DEV_OAUTH_CLIENT_IDS


def get_actual_client_id_from_development_client_id(client_id: str):
    if client_id.startswith(DEV_KEY_IDENTIFIER):
        return client_id.split(DEV_KEY_IDENTIFIER, 1)[1]
    return client_id


class APIImplementation(APIInterface):
    async def authorisation_url_get(self, provider: Provider, api_options: APIOptions, user_context: Dict[str, Any]) -> AuthorisationUrlGetResponse:
        authorisation_url_info = provider.get_authorisation_redirect_api_info(user_context)

        params: Dict[str, str] = {}
        for key, value in authorisation_url_info.params.items():
            params[key] = value if not callable(
                value) else value(api_options.request)

        redirect_uri = provider.get_redirect_uri(user_context)
        if redirect_uri is not None and not is_using_oauth_development_client_id(provider.client_id):
            # the backend wants to set the redirectURI - so we set that here.
            # we add the not development keys because the oauth provider will
            # redirect to supertokens.io's URL which will redirect the app
            # to the the user's website, which will handle the callback as usual.
            # If we add this, then instead, the supertokens' site will redirect
            # the user to this API layer, which is not needed.
            params['redirect_uri'] = redirect_uri

        auth_url = authorisation_url_info.url
        if is_using_oauth_development_client_id(provider.client_id):
            params['actual_redirect_uri'] = authorisation_url_info.url

            for k, v in params.items():
                if v == provider.client_id:
                    params[k] = get_actual_client_id_from_development_client_id(
                        provider.client_id)
            auth_url = DEV_OAUTH_AUTHORIZATION_URL

        query_string = urlencode(params)

        url = auth_url + '?' + query_string
        return AuthorisationUrlGetOkResponse(url)

    async def sign_in_up_post(self, provider: Provider, code: str, redirect_uri: str, client_id: Union[str, None], auth_code_response: Union[Dict[str, Any], None], api_options: APIOptions, user_context: Dict[str, Any]) -> SignInUpPostResponse:

        redirect_uri_from_provider = provider.get_redirect_uri(user_context)
        if is_using_oauth_development_client_id(provider.client_id):
            redirect_uri = DEV_OAUTH_REDIRECT_URL
        elif redirect_uri_from_provider is not None:
            # we overwrite the redirectURI provided by the frontend
            # since the backend wants to take charge of setting this.
            redirect_uri = redirect_uri_from_provider
        try:
            if auth_code_response is None:
                access_token_api_info = provider.get_access_token_api_info(
                    redirect_uri, code, user_context)
                if is_using_oauth_development_client_id(provider.client_id):
                    for k, _ in access_token_api_info.params.items():
                        if access_token_api_info.params[k] == provider.client_id:
                            access_token_api_info.params[k] = get_actual_client_id_from_development_client_id(
                                provider.client_id)
                headers = {
                    'Accept': 'application/json',
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
                async with AsyncClient() as client:
                    access_token_response = await client.post(access_token_api_info.url, data=access_token_api_info.params, headers=headers)  # type: ignore
                    access_token_response = access_token_response.json()
            else:
                access_token_response = auth_code_response
        except Exception as e:
            raise_general_exception(e)

        user_info: UserInfo
        try:
            user_info = await provider.get_profile_info(access_token_response, user_context)
        except Exception as e:
            return SignInUpPostFieldErrorResponse(str(e))
        email = user_info.email.id if user_info.email is not None else None
        email_verified = user_info.email.is_verified if user_info.email is not None else None
        if email is None or email_verified is None:
            return SignInUpPostNoEmailGivenByProviderResponse()

        signinup_response = await api_options.recipe_implementation.sign_in_up(provider.id, user_info.user_id, email, email_verified, user_context)

        if signinup_response.is_field_error:
            if signinup_response.error is None:
                raise Exception("Should never come here")
            return SignInUpPostFieldErrorResponse(signinup_response.error)

        if signinup_response.user is None:
            raise Exception("Should never come here")

        if email_verified:
            token_response = await api_options.email_verification_recipe_implementation.create_email_verification_token(user_id=signinup_response.user.user_id, email=signinup_response.user.email, user_context=user_context)

            if token_response.is_ok:
                if token_response.token is None:
                    raise Exception("Should never come here")
                await api_options.email_verification_recipe_implementation.verify_email_using_token(token=token_response.token, user_context=user_context)

        user = signinup_response.user
        if user is None or signinup_response.created_new_user is None:
            raise Exception("Should never come here")
        session = await create_new_session(api_options.request, user.user_id, user_context)

        return SignInUpPostOkResponse(
            user, signinup_response.created_new_user, access_token_response, session)

    async def apple_redirect_handler_post(self, code: str, state: str, api_options: APIOptions, user_context: Dict[str, Any]):
        app_info = api_options.app_info
        redirect_uri = app_info.website_domain.get_as_string_dangerous(
        ) + app_info.website_base_path.get_as_string_dangerous() + '/callback/apple?state=' + state + '&code=' + code
        html_content = '<html><head><script>window.location.replace("' + \
            redirect_uri + '");</script></head></html>'
        api_options.response.set_html_content(html_content)
