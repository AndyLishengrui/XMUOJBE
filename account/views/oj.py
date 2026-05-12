import os
from datetime import timedelta
from importlib import import_module

import qrcode
from django.conf import settings
from django.contrib import auth
from django.db.models import Q, Count
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from otpauth import OtpAuth

from contest.models import Contest, ACMContestRank, OIContestRank, ContestParticipation
from problem.models import Problem
from submission.models import Submission, JudgeStatus
from utils.constants import ContestRuleType
from options.options import SysOptions
from utils.api import APIView, validate_serializer, CSRFExemptAPIView
from utils.captcha import Captcha
from utils.shortcuts import rand_str, img2base64, datetime2str
from ..decorators import login_required
from ..models import User, UserProfile, AdminType
from ..serializers import (ApplyResetPasswordSerializer, ResetPasswordSerializer,
                           UserChangePasswordSerializer, UserLoginSerializer,
                           UserRegisterSerializer, UsernameOrEmailCheckSerializer,
                           RankInfoSerializer, UserChangeEmailSerializer, SSOSerializer)
from ..serializers import (TwoFactorAuthCodeSerializer, UserProfileSerializer,
                           EditUserProfileSerializer, ImageUploadForm)
from ..tasks import send_email_async


class UserProfileAPI(APIView):
    @method_decorator(ensure_csrf_cookie)
    def get(self, request, **kwargs):
        """
        判断是否登录， 若登录返回用户信息
        """
        user = request.user
        if not user.is_authenticated:
            return self.success()
        show_real_name = False
        username = request.GET.get("username")
        try:
            if username:
                user = User.objects.get(username=username, is_disabled=False)
            else:
                user = request.user
                # api返回的是自己的信息，可以返real_name
                show_real_name = True
        except User.DoesNotExist:
            return self.error("User does not exist")
        return self.success(UserProfileSerializer(user.userprofile, show_real_name=show_real_name).data)

    @validate_serializer(EditUserProfileSerializer)
    @login_required
    def put(self, request):
        data = request.data
        user_profile = request.user.userprofile
        for k, v in data.items():
            setattr(user_profile, k, v)
        user_profile.save()
        return self.success(UserProfileSerializer(user_profile, show_real_name=True).data)


class AvatarUploadAPI(APIView):
    request_parsers = ()

    @login_required
    def post(self, request):
        form = ImageUploadForm(request.POST, request.FILES)
        if form.is_valid():
            avatar = form.cleaned_data["image"]
        else:
            return self.error("Invalid file content")
        if avatar.size > 2 * 1024 * 1024:
            return self.error("Picture is too large")
        suffix = os.path.splitext(avatar.name)[-1].lower()
        if suffix not in [".gif", ".jpg", ".jpeg", ".bmp", ".png"]:
            return self.error("Unsupported file format")

        name = rand_str(10) + suffix
        with open(os.path.join(settings.AVATAR_UPLOAD_DIR, name), "wb") as img:
            for chunk in avatar:
                img.write(chunk)
        user_profile = request.user.userprofile

        user_profile.avatar = f"{settings.AVATAR_URI_PREFIX}/{name}"
        user_profile.save()
        return self.success("Succeeded")


class TwoFactorAuthAPI(APIView):
    @login_required
    def get(self, request):
        """
        Get QR code
        """
        user = request.user
        if user.two_factor_auth:
            return self.error("2FA is already turned on")
        token = rand_str()
        user.tfa_token = token
        user.save()

        label = f"{SysOptions.website_name_shortcut}:{user.username}"
        image = qrcode.make(OtpAuth(token).to_uri("totp", label, SysOptions.website_name.replace(" ", "")))
        return self.success(img2base64(image))

    @login_required
    @validate_serializer(TwoFactorAuthCodeSerializer)
    def post(self, request):
        """
        Open 2FA
        """
        code = request.data["code"]
        user = request.user
        if OtpAuth(user.tfa_token).valid_totp(code):
            user.two_factor_auth = True
            user.save()
            return self.success("Succeeded")
        else:
            return self.error("Invalid code")

    @login_required
    @validate_serializer(TwoFactorAuthCodeSerializer)
    def put(self, request):
        code = request.data["code"]
        user = request.user
        if not user.two_factor_auth:
            return self.error("2FA is already turned off")
        if OtpAuth(user.tfa_token).valid_totp(code):
            user.two_factor_auth = False
            user.save()
            return self.success("Succeeded")
        else:
            return self.error("Invalid code")


class CheckTFARequiredAPI(APIView):
    @validate_serializer(UsernameOrEmailCheckSerializer)
    def post(self, request):
        """
        Check TFA is required
        """
        data = request.data
        result = False
        if data.get("username"):
            try:
                user = User.objects.get(username=data["username"])
                result = user.two_factor_auth
            except User.DoesNotExist:
                pass
        return self.success({"result": result})


class UserLoginAPI(APIView):
    @validate_serializer(UserLoginSerializer)
    def post(self, request):
        """
        User login api
        """
        data = request.data
        user = auth.authenticate(username=data["username"], password=data["password"])
        # None is returned if username or password is wrong
        if user:
            if user.is_disabled:
                return self.error("Your account has been disabled")
            if not user.two_factor_auth:
                auth.login(request, user)
                return self.success("Succeeded")

            # `tfa_code` not in post data
            if user.two_factor_auth and "tfa_code" not in data:
                return self.error("tfa_required")

            if OtpAuth(user.tfa_token).valid_totp(data["tfa_code"]):
                auth.login(request, user)
                return self.success("Succeeded")
            else:
                return self.error("Invalid two factor verification code")
        else:
            return self.error("Invalid username or password")


class UserLogoutAPI(APIView):
    def get(self, request):
        auth.logout(request)
        return self.success()


class UsernameOrEmailCheck(APIView):
    @validate_serializer(UsernameOrEmailCheckSerializer)
    def post(self, request):
        """
        check username or email is duplicate
        """
        data = request.data
        # True means already exist.
        result = {
            "username": False,
            "email": False
        }
        if data.get("username"):
            result["username"] = User.objects.filter(username=data["username"].lower()).exists()
        if data.get("email"):
            result["email"] = User.objects.filter(email=data["email"].lower()).exists()
        return self.success(result)


class UserRegisterAPI(APIView):
    @validate_serializer(UserRegisterSerializer)
    def post(self, request):
        """
        User register api
        """
        if not SysOptions.allow_register:
            return self.error("Register function has been disabled by admin")

        data = request.data
        data["username"] = data["username"].lower()
        data["email"] = data["email"].lower()
        captcha = Captcha(request)
        if not captcha.check(data["captcha"]):
            return self.error("Invalid captcha")
        if User.objects.filter(username=data["username"]).exists():
            return self.error("Username already exists")
        if User.objects.filter(email=data["email"]).exists():
            return self.error("Email already exists")
        user = User.objects.create(username=data["username"], email=data["email"])
        user.set_password(data["password"])
        user.save()
        UserProfile.objects.create(user=user)
        return self.success("Succeeded")


class UserChangeEmailAPI(APIView):
    @validate_serializer(UserChangeEmailSerializer)
    @login_required
    def post(self, request):
        data = request.data
        user = auth.authenticate(username=request.user.username, password=data["password"])
        if user:
            if user.two_factor_auth:
                if "tfa_code" not in data:
                    return self.error("tfa_required")
                if not OtpAuth(user.tfa_token).valid_totp(data["tfa_code"]):
                    return self.error("Invalid two factor verification code")
            data["new_email"] = data["new_email"].lower()
            if User.objects.filter(email=data["new_email"]).exists():
                return self.error("The email is owned by other account")
            user.email = data["new_email"]
            user.save()
            return self.success("Succeeded")
        else:
            return self.error("Wrong password")


class UserChangePasswordAPI(APIView):
    @validate_serializer(UserChangePasswordSerializer)
    @login_required
    def post(self, request):
        """
        User change password api
        """
        data = request.data
        username = request.user.username
        user = auth.authenticate(username=username, password=data["old_password"])
        if user:
            if user.two_factor_auth:
                if "tfa_code" not in data:
                    return self.error("tfa_required")
                if not OtpAuth(user.tfa_token).valid_totp(data["tfa_code"]):
                    return self.error("Invalid two factor verification code")
            user.set_password(data["new_password"])
            user.save()
            return self.success("Succeeded")
        else:
            return self.error("Invalid old password")


class ApplyResetPasswordAPI(APIView):
    @validate_serializer(ApplyResetPasswordSerializer)
    def post(self, request):
        if request.user.is_authenticated:
            return self.error("You have already logged in, are you kidding me? ")
        data = request.data
        captcha = Captcha(request)
        if not captcha.check(data["captcha"]):
            return self.error("Invalid captcha")
        try:
            user = User.objects.get(email__iexact=data["email"])
        except User.DoesNotExist:
            return self.error("User does not exist")
        if user.reset_password_token_expire_time and 0 < int(
                (user.reset_password_token_expire_time - now()).total_seconds()) < 20 * 60:
            return self.error("You can only reset password once per 20 minutes")
        user.reset_password_token = rand_str()
        user.reset_password_token_expire_time = now() + timedelta(minutes=20)
        user.save()
        render_data = {
            "username": user.username,
            "website_name": SysOptions.website_name,
            "link": f"{SysOptions.website_base_url}/reset-password/{user.reset_password_token}"
        }
        email_html = render_to_string("reset_password_email.html", render_data)
        send_email_async.send(from_name=SysOptions.website_name_shortcut,
                              to_email=user.email,
                              to_name=user.username,
                              subject="Reset your password",
                              content=email_html)
        return self.success("Succeeded")


class ResetPasswordAPI(APIView):
    @validate_serializer(ResetPasswordSerializer)
    def post(self, request):
        data = request.data
        captcha = Captcha(request)
        if not captcha.check(data["captcha"]):
            return self.error("Invalid captcha")
        try:
            user = User.objects.get(reset_password_token=data["token"])
        except User.DoesNotExist:
            return self.error("Token does not exist")
        if user.reset_password_token_expire_time < now():
            return self.error("Token has expired")
        user.reset_password_token = None
        user.two_factor_auth = False
        user.set_password(data["password"])
        user.save()
        return self.success("Succeeded")


class SessionManagementAPI(APIView):
    @login_required
    def get(self, request):
        engine = import_module(settings.SESSION_ENGINE)
        session_store = engine.SessionStore
        current_session = request.session.session_key
        session_keys = request.user.session_keys
        result = []
        modified = False
        for key in session_keys[:]:
            session = session_store(key)
            # session does not exist or is expiry
            if not session._session:
                session_keys.remove(key)
                modified = True
                continue

            s = {}
            if current_session == key:
                s["current_session"] = True
            s["ip"] = session["ip"]
            s["user_agent"] = session["user_agent"]
            s["last_activity"] = datetime2str(session["last_activity"])
            s["session_key"] = key
            result.append(s)
        if modified:
            request.user.save()
        return self.success(result)

    @login_required
    def delete(self, request):
        session_key = request.GET.get("session_key")
        if not session_key:
            return self.error("Parameter Error")
        request.session.delete(session_key)
        if session_key in request.user.session_keys:
            request.user.session_keys.remove(session_key)
            request.user.save()
            return self.success("Succeeded")
        else:
            return self.error("Invalid session_key")


class UserRankAPI(APIView):
    def get(self, request):
        rule_type = request.GET.get("rule")
        if rule_type not in ContestRuleType.choices():
            rule_type = ContestRuleType.ACM
        profiles = UserProfile.objects.filter(user__admin_type=AdminType.REGULAR_USER, user__is_disabled=False) \
            .select_related("user")
        if rule_type == ContestRuleType.ACM:
            profiles = profiles.filter(submission_number__gt=0).order_by("-accepted_number", "submission_number")
        else:
            profiles = profiles.filter(total_score__gt=0).order_by("-total_score")
        return self.success(self.paginate_data(request, profiles, RankInfoSerializer))


class ProfileProblemDisplayIDRefreshAPI(APIView):
    @login_required
    def get(self, request):
        from submission.models import JudgeStatus

        profile = request.user.userprofile
        acm_root = profile.acm_problems_status or {}
        oi_root = profile.oi_problems_status or {}
        acm_problems = acm_root.get("problems", {}) or {}
        oi_problems = oi_root.get("problems", {}) or {}

        ids = []
        ids.extend([str(i) for i in acm_problems.keys()])
        ids.extend([str(i) for i in oi_problems.keys()])

        if not ids:
            return self.success({"removed": 0, "accepted_number": profile.accepted_number, "total_score": profile.total_score})

        valid_rows = Problem.objects.filter(
            id__in=ids,
            contest_id__isnull=True,
            visible=True
        ).values("id", "_id")
        valid_map = {str(row["id"]): row["_id"] for row in valid_rows}

        new_acm = {}
        for pid, item in acm_problems.items():
            spid = str(pid)
            display_id = valid_map.get(spid)
            if not display_id or not isinstance(item, dict):
                continue
            new_item = dict(item)
            new_item["_id"] = display_id
            new_acm[spid] = new_item

        new_oi = {}
        for pid, item in oi_problems.items():
            spid = str(pid)
            display_id = valid_map.get(spid)
            if not display_id or not isinstance(item, dict):
                continue
            new_item = dict(item)
            new_item["_id"] = display_id
            new_item["score"] = int(new_item.get("score", 0) or 0)
            new_oi[spid] = new_item

        acm_accepted = sum(1 for item in new_acm.values() if item.get("status") == JudgeStatus.ACCEPTED)
        oi_accepted = sum(1 for item in new_oi.values() if item.get("status") == JudgeStatus.ACCEPTED)
        total_score = sum(item.get("score", 0) for item in new_oi.values())

        old_count = len(acm_problems) + len(oi_problems)
        new_count = len(new_acm) + len(new_oi)

        acm_root["problems"] = new_acm
        oi_root["problems"] = new_oi
        profile.acm_problems_status = acm_root
        profile.oi_problems_status = oi_root
        profile.accepted_number = acm_accepted + oi_accepted
        profile.total_score = total_score
        profile.save(update_fields=["acm_problems_status", "oi_problems_status", "accepted_number", "total_score"])

        return self.success({
            "removed": max(old_count - new_count, 0),
            "accepted_number": profile.accepted_number,
            "total_score": profile.total_score
        })


class UserContestSummaryAPI(APIView):
    """
    Contest submissions within each experiment are recorded exclusively in the
    ACMContestRank / OIContestRank tables (via submission_info JSON). The global
    submission table is NOT used here — it only covers public-problem submissions.
    """

    @staticmethod
    def _get_target_user(request):
        username = request.GET.get("username")
        if not username and request.user.is_authenticated:
            return request.user
        if not username:
            raise User.DoesNotExist
        return User.objects.get(username=username, is_disabled=False)

    @staticmethod
    def _acm_rank_position(contest, rank):
        better_count = ACMContestRank.objects.filter(
            contest=contest,
            user__admin_type=AdminType.REGULAR_USER,
            user__is_disabled=False
        ).filter(
            Q(accepted_number__gt=rank.accepted_number) |
            Q(accepted_number=rank.accepted_number, total_time__lt=rank.total_time)
        ).count()
        return better_count + 1

    @staticmethod
    def _oi_rank_position(contest, rank):
        better_count = OIContestRank.objects.filter(
            contest=contest,
            user__admin_type=AdminType.REGULAR_USER,
            user__is_disabled=False,
            total_score__gt=rank.total_score
        ).count()
        return better_count + 1

    def get(self, request):
        if not request.GET.get("username") and not request.user.is_authenticated:
            return self.error("Please login first", "permission-denied")
        try:
            target_user = self._get_target_user(request)
        except User.DoesNotExist:
            return self.error("User does not exist")

        try:
            offset = max(int(request.GET.get("offset", 0)), 0)
            limit = max(int(request.GET.get("limit", 20)), 1)
        except ValueError:
            return self.error("Parameter error")

        participations = list(ContestParticipation.objects.filter(user=target_user)
                              .values("contest_id", "submission_number", "accepted_number", "total_score"))
        participation_map = {item["contest_id"]: item for item in participations}

        # Keep backward compatibility for historical data that may have rank rows
        # but no participation rows yet.
        acm_ranks = list(ACMContestRank.objects.filter(user=target_user).values(
            "contest_id", "submission_number", "accepted_number", "total_time"
        ))
        oi_ranks = list(OIContestRank.objects.filter(user=target_user).values(
            "contest_id", "submission_number", "total_score"
        ))

        # Some historical data only exists in submission table (without rank/participation).
        submission_fallback_rows = list(
            Submission.objects.filter(user_id=target_user.id, contest_id__isnull=False)
            .order_by()
            .values("contest_id")
            .annotate(submission_number=Count("id"))
        )
        accepted_fallback_rows = list(
            Submission.objects.filter(
                user_id=target_user.id,
                contest_id__isnull=False,
                result=JudgeStatus.ACCEPTED
            )
            .order_by()
            .values("contest_id")
            .annotate(accepted_number=Count("problem_id", distinct=True))
        )
        submission_fallback_map = {
            item["contest_id"]: {
                "submission_number": item["submission_number"],
                "accepted_number": 0
            }
            for item in submission_fallback_rows
        }
        for item in accepted_fallback_rows:
            contest_id = item["contest_id"]
            if contest_id not in submission_fallback_map:
                submission_fallback_map[contest_id] = {"submission_number": 0, "accepted_number": 0}
            submission_fallback_map[contest_id]["accepted_number"] = item["accepted_number"]

        acm_rank_map = {item["contest_id"]: item for item in acm_ranks}
        oi_rank_map = {item["contest_id"]: item for item in oi_ranks}

        contest_ids = set(participation_map.keys()) | set(acm_rank_map.keys()) | set(oi_rank_map.keys()) | set(submission_fallback_map.keys())

        contests = Contest.objects.filter(id__in=contest_ids, visible=True).order_by("-end_time", "-id")
        total = contests.count()
        contests = list(contests[offset: offset + limit])

        results = []
        for contest in contests:
            item = {
                "contest_id": contest.id,
                "contest_title": contest.title,
                "rule_type": contest.rule_type,
                "start_time": datetime2str(contest.start_time),
                "end_time": datetime2str(contest.end_time),
                "my_rank": None,
                "submission_count": 0,
                "ac_count": 0,
                "total_score": 0
            }
            participation = participation_map.get(contest.id)
            if participation:
                item["submission_count"] = participation["submission_number"]
                item["ac_count"] = participation["accepted_number"]
                item["total_score"] = participation["total_score"]

            fallback = submission_fallback_map.get(contest.id)
            if fallback:
                item["submission_count"] = max(item["submission_count"], fallback["submission_number"])
                item["ac_count"] = max(item["ac_count"], fallback["accepted_number"])

            if contest.rule_type == ContestRuleType.ACM:
                rank_data = acm_rank_map.get(contest.id)
                if rank_data:
                    rank = ACMContestRank(
                        contest_id=contest.id,
                        user_id=target_user.id,
                        submission_number=rank_data["submission_number"],
                        accepted_number=rank_data["accepted_number"],
                        total_time=rank_data["total_time"]
                    )
                    item["my_rank"] = self._acm_rank_position(contest, rank)
                    item["submission_count"] = rank_data["submission_number"]
                    item["ac_count"] = rank_data["accepted_number"]
            else:
                rank_data = oi_rank_map.get(contest.id)
                if rank_data:
                    rank = OIContestRank(
                        contest_id=contest.id,
                        user_id=target_user.id,
                        submission_number=rank_data["submission_number"],
                        total_score=rank_data["total_score"]
                    )
                    item["my_rank"] = self._oi_rank_position(contest, rank)
                    item["submission_count"] = rank_data["submission_number"]
                    item["total_score"] = rank_data["total_score"]
            results.append(item)

        return self.success({"total": total, "results": results})


class UserContestDetailAPI(APIView):
    """
    Contest/experiment detail for a user. All per-problem status is derived from
    ACMContestRank.submission_info or OIContestRank.submission_info JSON fields.
    ACM submission_info format: {"<problem_id>": {"is_ac": bool, "ac_time": int, "error_number": int}}
    OI  submission_info format: {"<problem_id>": <score_int>}
    The global submission table is NOT used here.
    """

    @staticmethod
    def _get_target_user(request):
        username = request.GET.get("username")
        if not username and request.user.is_authenticated:
            return request.user
        if not username:
            raise User.DoesNotExist
        return User.objects.get(username=username, is_disabled=False)

    @staticmethod
    def _acm_rank_position(contest, rank):
        better_count = ACMContestRank.objects.filter(
            contest=contest,
            user__admin_type=AdminType.REGULAR_USER,
            user__is_disabled=False
        ).filter(
            Q(accepted_number__gt=rank.accepted_number) |
            Q(accepted_number=rank.accepted_number, total_time__lt=rank.total_time)
        ).count()
        return better_count + 1

    @staticmethod
    def _oi_rank_position(contest, rank):
        better_count = OIContestRank.objects.filter(
            contest=contest,
            user__admin_type=AdminType.REGULAR_USER,
            user__is_disabled=False,
            total_score__gt=rank.total_score
        ).count()
        return better_count + 1

    def get(self, request):
        if not request.GET.get("username") and not request.user.is_authenticated:
            return self.error("Please login first", "permission-denied")
        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error("Parameter error")

        try:
            target_user = self._get_target_user(request)
        except User.DoesNotExist:
            return self.error("User does not exist")

        try:
            contest = Contest.objects.get(id=contest_id, visible=True)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        participation = ContestParticipation.objects.filter(contest=contest, user=target_user).first()
        my_rank = None
        submission_count = participation.submission_number if participation else 0
        ac_count = participation.accepted_number if participation else 0
        total_score = participation.total_score if participation else 0
        submission_info = {}

        if contest.rule_type == ContestRuleType.ACM:
            rank = ACMContestRank.objects.filter(contest=contest, user=target_user).first()
            if rank:
                my_rank = self._acm_rank_position(contest, rank)
                submission_count = max(submission_count, rank.submission_number or 0)
                ac_count = max(ac_count, rank.accepted_number or 0)
                submission_info = rank.submission_info or {}
        else:
            rank = OIContestRank.objects.filter(contest=contest, user=target_user).first()
            if rank:
                my_rank = self._oi_rank_position(contest, rank)
                submission_count = max(submission_count, rank.submission_number or 0)
                total_score = max(total_score, rank.total_score or 0)
                submission_info = rank.submission_info or {}

        # Fallback: if rank rows are missing/incomplete, use calibrated contest
        # problem status cached on user profile.
        if not submission_info:
            profile = target_user.userprofile
            if contest.rule_type == ContestRuleType.ACM:
                contest_problems = (profile.acm_problems_status or {}).get("contest_problems", {})
                for pid, item in contest_problems.items():
                    if isinstance(item, dict):
                        submission_info[pid] = {
                            "is_ac": item.get("status") == 0,
                            "error_number": 0,
                            "ac_time": None
                        }
            else:
                contest_problems = (profile.oi_problems_status or {}).get("contest_problems", {})
                for pid, item in contest_problems.items():
                    if isinstance(item, dict):
                        submission_info[pid] = item.get("score", 100 if item.get("status") == 0 else 0)

        # Last fallback for historical records: derive from contest submissions.
        if not submission_info:
            contest_submissions = list(
                Submission.objects.filter(user_id=target_user.id, contest_id=contest.id)
                .values("problem_id", "result", "create_time", "statistic_info")
                .order_by("create_time")
            )
            submission_count = max(submission_count, len(contest_submissions))

            if contest.rule_type == ContestRuleType.ACM:
                ac_problem_count = 0
                for row in contest_submissions:
                    pid = str(row["problem_id"])
                    if pid not in submission_info:
                        submission_info[pid] = {"is_ac": False, "error_number": 0, "ac_time": None}
                    if submission_info[pid]["is_ac"]:
                        continue
                    if row["result"] == JudgeStatus.ACCEPTED:
                        submission_info[pid]["is_ac"] = True
                        if row["create_time"] and contest.start_time:
                            submission_info[pid]["ac_time"] = int((row["create_time"] - contest.start_time).total_seconds())
                        ac_problem_count += 1
                    else:
                        submission_info[pid]["error_number"] += 1
                ac_count = max(ac_count, ac_problem_count)
            else:
                for row in contest_submissions:
                    pid = str(row["problem_id"])
                    score = 0
                    stat = row.get("statistic_info") or {}
                    if isinstance(stat, dict):
                        score = int(stat.get("score") or 0)
                    if row["result"] == JudgeStatus.ACCEPTED:
                        score = max(score, 100)
                    submission_info[pid] = max(submission_info.get(pid, 0), score)
                total_score = max(total_score, sum(submission_info.values()))
                ac_count = max(ac_count, sum(1 for v in submission_info.values() if v > 0))

        if not participation and not submission_info:
            return self.error("No participation record in this contest")

        problems = list(Problem.objects.filter(contest_id=contest.id, visible=True)
                        .values("id", "_id", "title")
                        .order_by("_id"))

        problem_items = []
        for problem in problems:
            info = submission_info.get(str(problem["id"]))
            if contest.rule_type == ContestRuleType.ACM:
                # info = {"is_ac": bool, "ac_time": int, "error_number": int, ...}
                is_ac = bool(info and info.get("is_ac"))
                error_number = info.get("error_number", 0) if info else 0
                ac_time = info.get("ac_time") if (info and is_ac) else None
                problem_items.append({
                    "display_id": problem["_id"],
                    "title": problem["title"],
                    "is_ac": is_ac,
                    "error_number": error_number,
                    "ac_time": ac_time,
                    "best_score": None
                })
            else:
                # info = score (int) or None
                best_score = info if info is not None else 0
                is_ac = best_score > 0
                problem_items.append({
                    "display_id": problem["_id"],
                    "title": problem["title"],
                    "is_ac": is_ac,
                    "error_number": 0,
                    "ac_time": None,
                    "best_score": best_score
                })

        return self.success({
            "contest_id": contest.id,
            "contest_title": contest.title,
            "rule_type": contest.rule_type,
            "my_rank": my_rank,
            "submission_count": submission_count,
            "ac_count": ac_count,
            "total_score": total_score,
            "problem_items": problem_items
        })


class ContestCalibrateAPI(APIView):
    """
    Manually trigger calibration for a specific contest.
    This allows users to re-sync data from rank tables into participation record.
    """

    @login_required
    def post(self, request):
        contest_id = request.data.get("contest_id")
        if not contest_id:
            return self.error("Parameter error")

        try:
            contest = Contest.objects.get(id=contest_id, visible=True)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        user = request.user

        try:
            # Force calibration regardless of previous state. If participation
            # does not exist yet, calibrate_once will create it automatically.
            participation = ContestParticipation.calibrate_once(user, contest, force=True)
            return self.success({
                "success": True,
                "is_calibrated": participation.is_calibrated,
                "submission_number": participation.submission_number,
                "accepted_number": participation.accepted_number,
                "total_score": participation.total_score
            })
        except Exception as e:
            return self.error(f"Calibration failed: {str(e)}")


class OpenAPIAppkeyAPI(APIView):
    @login_required
    def post(self, request):
        user = request.user
        if not user.open_api:
            return self.error("OpenAPI function is truned off for you")
        api_appkey = rand_str()
        user.open_api_appkey = api_appkey
        user.save()
        return self.success({"appkey": api_appkey})


class SSOAPI(CSRFExemptAPIView):
    @login_required
    def get(self, request):
        token = rand_str()
        request.user.auth_token = token
        request.user.save()
        return self.success({"token": token})

    @method_decorator(csrf_exempt)
    @validate_serializer(SSOSerializer)
    def post(self, request):
        try:
            user = User.objects.get(auth_token=request.data["token"])
        except User.DoesNotExist:
            return self.error("User does not exist")
        return self.success({"username": user.username, "avatar": user.userprofile.avatar, "admin_type": user.admin_type})
