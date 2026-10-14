from django.contrib import admin
from .models import Collaborateur
from .models import Departement ,MaquetteN1 , HistoriqueMaquetteN1

admin.site.register(Collaborateur)
admin.site.register(Departement)
admin.site.register(HistoriqueMaquetteN1)
admin.site.register(MaquetteN1)