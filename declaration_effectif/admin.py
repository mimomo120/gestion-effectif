from django.contrib import admin
from .models import declaration_effectif,Alert,historique
# Register your models here.
admin.site.register(declaration_effectif)
admin.site.register(Alert)
admin.site.register(historique)