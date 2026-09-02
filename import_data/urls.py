# import_data/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("import/", views.importer_fichiers_combines, name="importer_fichier"),
    path('export-effectif-reel/', views.export_effectif_reel, name='export_effectif_reel'),
]