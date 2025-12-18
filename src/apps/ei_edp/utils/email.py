from src.apps.copo_core.models import User, AssociatedProfileType
from common.utils.copo_email import CopoEmail
from common.dal.profile_da import Profile
from common.utils.logger import Logger
from django.conf import settings
from django_tools.middlewares import ThreadLocal

logger = Logger()

class Email:

   
    def notify_shared_profile_to_not_exist_user(self, profile, customer_emails_token):
        subj = "COPO Profile Shared with You"
        uri = ThreadLocal.get_current_request().build_absolute_uri('/')
        if settings.ENVIRONMENT_TYPE != "prod":
            subj =  "Non Prod : " + subj

        if profile and customer_emails_token:
            for token, email in customer_emails_token.items():
                    msg = f"<h4>COPO Profile Shared with You</h4><p>A COPO profile named '{profile.get('title', '')}' has been shared with you. Please use the link below to create your COPO account and access the profile.</p><p><a href='{uri}/join_shared_profile/{str(profile["_id"])}/{token}'>Create COPO Account</a></p>"
                    CopoEmail().send(to=[email], sub=subj, content=msg, html=True)

    def notify_shared_profile_to_existing_user(self, profile, users):
        subj = "COPO Profile Shared with You"
        uri = ThreadLocal.get_current_request().build_absolute_uri('/')
        if settings.ENVIRONMENT_TYPE != "prod":
            subj =  "Non Prod : " + subj

        if profile and users:
            for user in users:
                msg = f"Dear {user.first_name}, <h4>COPO Profile Shared with You</h4><p>A COPO profile named '{profile.get('title', '')}' has been shared with you. Please log in to your COPO account to access the profile.</p>"
                CopoEmail().send(to=[ user.email], sub=subj, content=msg, html=True)
    