from django.conf.urls import url

from ..views.admin import (UserAdminAPI, GenerateUserAPI, ChangeUserpasswordAPI,
                           BatchResetPasswordAPI, ResetUserPasswordAPI)

urlpatterns = [
    url(r"^user/?$", UserAdminAPI.as_view(), name="user_admin_api"),
    url(r"^generate_user/?$", GenerateUserAPI.as_view(), name="generate_user_api"),
    url(r"^change_userpassword/?$", ChangeUserpasswordAPI.as_view(), name="change_userpassword_api"),
    url(r"^batch_reset_password/?$", BatchResetPasswordAPI.as_view(), name="batch_reset_password_api"),
    url(r"^reset_user_password/?$", ResetUserPasswordAPI.as_view(), name="reset_user_password_api"),
]
