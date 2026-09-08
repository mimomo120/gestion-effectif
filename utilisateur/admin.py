from django.contrib import admin
from .models import utilisateur , LoginLog

# Register your models here.
admin.site.register(utilisateur)
admin.site.register(LoginLog)