# -*- coding: utf-8 -*-
"""
    flask.ext.security.core
    ~~~~~~~~~~~~~~~~~~~~~~~

    Flask-Security core module

    :copyright: (c) 2012 by Matt Wright.
    :license: MIT, see LICENSE for more details.
"""

from datetime import timedelta
from functools import wraps

from flask import current_app, Blueprint, redirect, request
from flask.ext.login import AnonymousUser as AnonymousUserBase, \
     UserMixin as BaseUserMixin, LoginManager, login_required, \
     current_user, login_url
from flask.ext.principal import Principal, RoleNeed, UserNeed, \
     Permission, identity_loaded
from flask.ext.wtf import Form, TextField, PasswordField, SubmitField, \
     HiddenField, Required, BooleanField, EqualTo, Email
from flask.ext.security import views, exceptions, utils
from passlib.context import CryptContext
from werkzeug.datastructures import ImmutableList


#: Default Flask-Security configuration
_default_config = {
    'SECURITY_URL_PREFIX': None,
    'SECURITY_FLASH_MESSAGES': True,
    'SECURITY_PASSWORD_HASH': 'plaintext',
    'SECURITY_AUTH_PROVIDER': 'flask.ext.security::AuthenticationProvider',
    'SECURITY_LOGIN_FORM': 'flask.ext.security::LoginForm',
    'SECURITY_REGISTER_FORM': 'flask.ext.security::RegisterForm',
    'SECURITY_AUTH_URL': '/auth',
    'SECURITY_LOGOUT_URL': '/logout',
    'SECURITY_REGISTER_URL': '/register',
    'SECURITY_RESET_URL': '/reset',
    'SECURITY_CONFIRM_URL': '/confirm',
    'SECURITY_LOGIN_VIEW': '/login',
    'SECURITY_POST_LOGIN_VIEW': '/',
    'SECURITY_POST_LOGOUT_VIEW': '/',
    'SECURITY_POST_REGISTER_VIEW': '/',
    'SECURITY_POST_CONFIRM_VIEW': '/',
    'SECURITY_RESET_PASSWORD_WITHIN': 10,
    'SECURITY_DEFAULT_ROLES': [],
    'SECURITY_LOGIN_WITHOUT_CONFIRMATION': True,
    'SECURITY_CONFIRM_EMAIL': False,
    'SECURITY_CONFIRM_EMAIL_WITHIN': '5 days',
    'SECURITY_EMAIL_SENDER': 'no-reply@localhost'
}


def roles_required(*roles):
    """View decorator which specifies that a user must have all the specified
    roles. Example::

        @app.route('/dashboard')
        @roles_required('admin', 'editor')
        def dashboard():
            return 'Dashboard'

    The current user must have both the `admin` role and `editor` role in order
    to view the page.

    :param args: The required roles.
    """
    perm = Permission(*[RoleNeed(role) for role in roles])

    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated():
                login_view = current_app.security.login_manager.login_view
                return redirect(login_url(login_view, request.url))

            if perm.can():
                return fn(*args, **kwargs)

            current_app.logger.debug('Identity does not provide the '
                                     'roles: %s' % [r for r in roles])
            return redirect(request.referrer or '/')
        return decorated_view
    return wrapper


def roles_accepted(*roles):
    """View decorator which specifies that a user must have at least one of the
    specified roles. Example::

        @app.route('/create_post')
        @roles_accepted('editor', 'author')
        def create_post():
            return 'Create Post'

    The current user must have either the `editor` role or `author` role in
    order to view the page.

    :param args: The possible roles.
    """
    perms = [Permission(RoleNeed(role)) for role in roles]

    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated():
                login_view = current_app.security.login_manager.login_view
                return redirect(login_url(login_view, request.url))

            for perm in perms:
                if perm.can():
                    return fn(*args, **kwargs)

            r1 = [r for r in roles]
            r2 = [r.name for r in current_user.roles]

            current_app.logger.debug('Current user does not provide a '
                'required role. Accepted: %s Provided: %s' % (r1, r2))

            utils.do_flash('You do not have permission to '
                           'view this resource', 'error')

            return redirect(request.referrer or '/')
        return decorated_view
    return wrapper


class RoleMixin(object):
    """Mixin for `Role` model definitions"""
    def __eq__(self, other):
        if isinstance(other, basestring):
            return self.name == other
        return self.name == other.name

    def __ne__(self, other):
        if isinstance(other, basestring):
            return self.name != other
        return self.name != other.name

    def __str__(self):
        return '<Role name=%s>' % self.name


class UserMixin(BaseUserMixin):
    """Mixin for `User` model definitions"""

    def is_active(self):
        """Returns `True` if the user is active."""
        return self.active

    def has_role(self, role):
        """Returns `True` if the user identifies with the specified role.

        :param role: A role name or `Role` instance"""
        return role in self.roles

    def __str__(self):
        ctx = (str(self.id), self.email)
        return '<User id=%s, email=%s>' % ctx


class AnonymousUser(AnonymousUserBase):
    def __init__(self):
        super(AnonymousUser, self).__init__()
        self.roles = ImmutableList()

    def has_role(self, *args):
        """Returns `False`"""
        return False


def load_user(user_id):
    try:
        return current_app.security.datastore.with_id(user_id)
    except Exception, e:
        current_app.logger.error('Error getting user: %s' % e)
        return None


def on_identity_loaded(sender, identity):
    if hasattr(current_user, 'id'):
        identity.provides.add(UserNeed(current_user.id))

    for role in current_user.roles:
        identity.provides.add(RoleNeed(role.name))

    identity.user = current_user


class Security(object):
    """The :class:`Security` class initializes the Flask-Security extension.

    :param app: The application.
    :param datastore: An instance of a user datastore.
    """
    def __init__(self, app=None, datastore=None, **kwargs):
        self.init_app(app, datastore, **kwargs)

    def init_app(self, app, datastore,
                 registerable=True, recoverable=False, template_folder=None):
        """Initializes the Flask-Security extension for the specified
        application and datastore implentation.

        :param app: The application.
        :param datastore: An instance of a user datastore.
        """
        if app is None or datastore is None:
            return

        for key, value in _default_config.items():
            app.config.setdefault(key, value)

        login_manager = LoginManager()
        login_manager.anonymous_user = AnonymousUser
        login_manager.login_view = utils.config_value(app, 'LOGIN_VIEW')
        login_manager.user_loader(load_user)
        login_manager.setup_app(app)

        Provider = utils.get_class_from_string(app, 'AUTH_PROVIDER')
        pw_hash = utils.config_value(app, 'PASSWORD_HASH')

        self.login_manager = login_manager
        self.pwd_context = CryptContext(schemes=[pw_hash], default=pw_hash)
        self.auth_provider = Provider(Form)
        self.principal = Principal(app)
        self.datastore = datastore
        self.LoginForm = utils.get_class_from_string(app, 'LOGIN_FORM')
        self.RegisterForm = utils.get_class_from_string(app, 'REGISTER_FORM')
        self.auth_url = utils.config_value(app, 'AUTH_URL')
        self.logout_url = utils.config_value(app, 'LOGOUT_URL')
        self.reset_url = utils.config_value(app, 'RESET_URL')
        self.register_url = utils.config_value(app, 'REGISTER_URL')
        self.confirm_url = utils.config_value(app, 'CONFIRM_URL')
        self.post_login_view = utils.config_value(app, 'POST_LOGIN_VIEW')
        self.post_logout_view = utils.config_value(app, 'POST_LOGOUT_VIEW')
        self.post_register_view = utils.config_value(app, 'POST_REGISTER_VIEW')
        self.post_confirm_view = utils.config_value(app, 'POST_CONFIRM_VIEW')
        self.reset_password_within = utils.config_value(app, 'RESET_PASSWORD_WITHIN')
        self.default_roles = utils.config_value(app, "DEFAULT_ROLES")
        self.login_without_confirmation = utils.config_value(app, 'LOGIN_WITHOUT_CONFIRMATION')
        self.confirm_email = utils.config_value(app, 'CONFIRM_EMAIL')
        self.email_sender = utils.config_value(app, 'EMAIL_SENDER')
        self.confirm_email_within_text = utils.config_value(app, 'CONFIRM_EMAIL_WITHIN')

        values = self.confirm_email_within_text.split()
        self.confirm_email_within = timedelta(**{values[1]: int(values[0])})

        identity_loaded.connect_via(app)(on_identity_loaded)

        bp = Blueprint('flask_security', __name__, template_folder='templates')

        bp.route(self.auth_url,
                 methods=['POST'],
                 endpoint='authenticate')(views.authenticate)

        bp.route(self.logout_url,
                 endpoint='logout')(login_required(views.logout))

        self.setup_register(bp) if registerable else None
        self.setup_reset(bp) if recoverable else None
        self.setup_confirm(bp) if self.confirm_email else None

        app.register_blueprint(bp,
            url_prefix=utils.config_value(app, 'URL_PREFIX'))

        app.security = self

    def setup_register(self, bp):
        bp.route(self.register_url,
                 methods=['POST'],
                 endpoint='register')(views.register)

    def setup_reset(self, bp):
        bp.route(self.reset_url,
                 methods=['POST'],
                 endpoint='reset')(views.reset)

    def setup_confirm(self, bp):
        bp.route(self.confirm_url, endpoint='confirm')(views.confirm)


class LoginForm(Form):
    """The default login form"""

    email = TextField("Email Address",
        validators=[Required(message="Email not provided")])
    password = PasswordField("Password",
        validators=[Required(message="Password not provided")])
    remember = BooleanField("Remember Me")
    next = HiddenField()
    submit = SubmitField("Login")

    def __init__(self, *args, **kwargs):
        super(LoginForm, self).__init__(*args, **kwargs)
        self.next.data = request.args.get('next', None)


class RegisterForm(Form):
    """The default register form"""

    email = TextField("Email Address",
        validators=[Required(message='Email not provided'), Email()])
    password = PasswordField("Password",
        validators=[Required(message="Password not provided")])
    password_confirm = PasswordField("Password",
        validators=[EqualTo('password', message="Password not provided")])

    def to_dict(self):
        return dict(email=self.email.data, password=self.password.data)


class AuthenticationProvider(object):
    """The default authentication provider implementation.

    :param login_form_class: The login form class to use when authenticating a
                             user
    """

    def __init__(self, login_form_class=None):
        self.login_form_class = login_form_class or LoginForm

    def login_form(self, formdata=None):
        """Returns an instance of the login form with the provided form.

        :param formdata: The incoming form data"""
        return self.login_form_class(formdata)

    def authenticate(self, form):
        """Processes an authentication request and returns a user instance if
        authentication is successful.

        :param form: An instance of a populated login form
        """
        if not form.validate():
            if form.email.errors:
                raise exceptions.BadCredentialsError(form.email.errors[0])
            if form.password.errors:
                raise exceptions.BadCredentialsError(form.password.errors[0])

        return self.do_authenticate(form.email.data, form.password.data)

    def do_authenticate(self, email, password):
        """Returns the authenticated user if authentication is successfull. If
        authentication fails an appropriate error is raised

        :param user_identifier: The user's identifier, usuall an email address
        :param password: The user's unencrypted password
        """
        try:
            user = current_app.security.datastore.find_user(email=email)
        except AttributeError, e:
            self.auth_error("Could not find user datastore: %s" % e)
        except exceptions.UserNotFoundError, e:
            raise exceptions.BadCredentialsError("Specified user does not exist")
        except Exception, e:
            self.auth_error('Unexpected authentication error: %s' % e)

        # compare passwords
        if current_app.security.pwd_context.verify(password, user.password):
            return user

        # bad match
        raise exceptions.BadCredentialsError("Password does not match")

    def auth_error(self, msg):
        """Sends an error log message and raises an authentication error.

        :param msg: An authentication error message"""
        current_app.logger.error(msg)
        raise exceptions.AuthenticationError(msg)
