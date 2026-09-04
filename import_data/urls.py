# import_data/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("import/", views.importer_fichiers_combines, name="importer_fichier"),
    path('export-effectif-reel/', views.export_effectif_reel, name='export_effectif_reel'),
    path("import/<int:import_id>/details/", views.import_details_json, name="import_details_json"),
]