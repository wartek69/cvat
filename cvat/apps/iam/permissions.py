# Copyright (C) 2021 Intel Corporation
#
# SPDX-License-Identifier: MIT

import operator

import requests
from django.conf import settings
from django.db.models import Q
from rest_framework.permissions import BasePermission

from cvat.apps.organizations.models import Membership, Organization

class OpenPolicyAgentPermission:
    def __init__(self, request, view, obj):
        self.request = request
        self.view = view
        self.obj = obj

        privilege = self.request.iam_context['privilege']
        organization = self.request.iam_context['organization']
        membership = self.request.iam_context['membership']
        user = self.request.user

        self.payload = {
            'input': {
                'auth': {
                    'user': {
                        'id': user.id,
                        'privilege': privilege.name,
                    },
                    'organization': {
                        'id': organization.id,
                        'is_owner': organization.owner.id == user.id,
                        'role': getattr(membership, 'role', None),
                    } if organization else None
                }
            }
        }

    def __bool__(self):
        r = requests.post(self.url, json=self.payload)
        return r.json()['result']

    def filter(self, queryset):
        url = self.url.replace('/allow', '/filter')
        r = requests.post(url, json=self.payload)
        qobjects = []
        ops_dict = {
            '|': operator.or_,
            '&': operator.and_,
            '~': operator.not_,
        }
        for token in r.json()['result']:
            if isinstance(token, str):
                val1 = qobjects.pop()
                if token == '~':
                    qobjects.append(ops_dict[token](val1))
                else:
                    val2 = qobjects.pop()
                    qobjects.append(ops_dict[token](val1, val2))
            else:
                qobjects.append(Q(**token))

        if qobjects:
            assert len(qobjects) == 1
        else:
            qobjects.append(Q())

        return queryset.filter(qobjects[0])


class ServerPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        permissions = []
        if view.basename == 'server':
            self = cls(request, view, obj)
            permissions.append(self)

        return permissions

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)
        self.url = settings.IAM_OPA_DATA_URL + '/server/allow'
        self.payload['input']['scope'] = self.scope

    @property
    def scope(self):
        return {
            'annotation_formats': 'VIEW',
            'about': 'VIEW',
            'plugins': 'VIEW',
            'exception': 'SEND_EXCEPTION',
            'logs': 'SEND_LOGS',
            'share': 'LIST_CONTENT'
        }.get(self.view.action, None)

class UserPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        permissions = []
        if view.basename == 'user':
            self = cls(request, view, obj)
            permissions.append(self)

        return permissions

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)
        self.url = settings.IAM_OPA_DATA_URL + '/users/allow'
        self.payload['input']['scope'] = self.scope
        if view.detail:
            self.payload['input']['resource'] = {
                'id': obj.id
            }

    @property
    def scope(self):
        return {
            'list': 'LIST',
            'self': 'VIEW_SELF',
            'retrieve': 'VIEW'
        }.get(self.view.action, None)


class LambdaPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        permissions = []
        if view.basename == 'function' or view.basename == 'request':
            self = cls(request, view, obj)
            permissions.append(self)

        return permissions

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)
        self.url = settings.IAM_OPA_DATA_URL + '/lambda/allow'
        self.payload['input']['scope'] = self.scope

    @property
    def scope(self):
        return {
            ('function', 'list'): 'LIST',
            ('function', 'retrieve'): 'VIEW',
            ('function', 'call'): 'CALL_ONLINE',
            ('request', 'create'): 'CALL_OFFLINE',
            ('request', 'list'): 'CALL_OFFLINE',
            ('request', 'retrieve'): 'CALL_OFFLINE',
            ('request', 'destroy'): 'CALL_OFFLINE',
        }.get((self.view.basename, self.view.action), None)


class OrganizationPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        permissions = []
        if view.basename == 'organization':
            self = cls(request, view, obj)
            permissions.append(self)

        return permissions

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)
        self.url = settings.IAM_OPA_DATA_URL + '/organizations/allow'
        self.payload['input']['scope'] = self.scope
        if view.detail:
            self.payload['input']['resource'] = self.resource
        self.payload['input']['user'] = {
            'num_resources': Organization.objects.filter(
                owner_id=self.request.user.id).count()
        }

    @property
    def scope(self):
        return {
            'list': 'LIST',
            'create': 'CREATE',
            'destroy': 'DELETE',
            'partial_update': 'UPDATE',
            'retrieve': 'VIEW'
        }.get(self.view.action, None)

    @property
    def resource(self):
        if self.obj:
            user = self.request.user
            membership = Membership.objects.filter(organization=self.obj, user=user).first()
            return {
                'id': self.obj.id,
                'is_owner': self.obj.owner.id == user.id,
                'owner': {
                    'id': self.obj.owner.id
                },
                'role': membership.role if membership else None,
            }
        else:
            return None


class MembershipPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        permissions = []
        if view.basename == 'membership':
            self = cls(request, view, obj)
            permissions.append(self)

        return permissions

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)
        self.url = settings.IAM_OPA_DATA_URL + '/memberships/allow'
        self.payload['input']['scope'] = self.scope
        if view.detail:
            self.payload['input']['resource'] = self.resource

    @property
    def scope(self):
        return {
            'list': 'LIST',
            'partial_update': 'CHANGE_ROLE',
            'retrieve': 'VIEW',
            'delete': 'DELETE'
        }.get(self.view.action, None)

    @property
    def resource(self):
        return {
            'role': self.obj.role,
            'user': {
                'id': self.obj.user.id
            }
        }



class InvitationPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        scope = None # filter, OPTIONS
        if getattr(view, 'action', None):
            scope = {
                'list': 'LIST',
                'create': 'CREATE',
                'destroy': 'DELETE',
                'partial_update': 'UPDATE',
                'retrieve': 'VIEW',
            }[view.action]

        return scope

    url = settings.IAM_OPA_DATA_URL + '/invitations/allow'

class CloudStoragePermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return super().get_scope(request, view, obj)

    url = settings.IAM_OPA_DATA_URL + '/cloudstorages/allow'

class ProjectPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return super().get_scope(request, view, obj)

    url = settings.IAM_OPA_DATA_URL + '/projects/allow'

class TaskPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return {
            'list': 'LIST',
            'destroy': 'DELETE',
        }[view.action]

    url = settings.IAM_OPA_DATA_URL + '/tasks/allow'

class JobPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        # TODO: check jobs and tasks from lambda calls
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return super().get_scope(request, view, obj)

    url = settings.IAM_OPA_DATA_URL + '/jobs/allow'


class CommentPermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return super().get_scope(request, view, obj)

    url = settings.IAM_OPA_DATA_URL + '/comments/allow'

class IssuePermission(OpenPolicyAgentPermission):
    @classmethod
    def create(cls, request, view, obj):
        return []

    def __init__(self, request, view, obj):
        super().__init__(request, view, obj)

    def get_scope(self, request, view, obj):
        return super().get_scope(request, view, obj)

    url = settings.IAM_OPA_DATA_URL + '/issues/allow'


class PolicyEnforcer(BasePermission):
    # pylint: disable=no-self-use
    def check_permission(self, request, view, obj):
        permissions = []
        for perm in OpenPolicyAgentPermission.__subclasses__():
            permissions.extend(perm.create(request, view, obj))

        return all(permissions)

    def has_permission(self, request, view):
        if not view.detail:
            return self.check_permission(request, view, None)
        else:
            return True # has_object_permision will be called later

    def has_object_permission(self, request, view, obj):
        return self.check_permission(request, view, obj)

