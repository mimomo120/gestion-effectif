from django.contrib import admin
from .models import Collaborateur
from .models import Departement ,Unite

admin.site.register(Collaborateur)
admin.site.register(Departement)
admin.site.register(Unite)