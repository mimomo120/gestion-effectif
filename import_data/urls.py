# import_data/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("import/", views.importer_fichier_Collaborateur, name="importer_fichier"),
]