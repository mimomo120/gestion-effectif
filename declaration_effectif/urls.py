from django.contrib import admin
from django.urls import include, path
from django.conf import settings


from  declaration_effectif import views

urlpatterns=[
    path('validation/',views.validation_view,name='validation'),
    path("historique/",views.histo_aff,name="historique"),
    path("valider_operateurs/",views.valider,name="valider_operateurs"),
    path("supprimer/",views.supprimer,name="supprimer"),
    path("afficher_modifier",views.afficher_modifier),
    path("validation_rg/",views.liste_ru_non_valides_Rg,name="liste_ru_non_valides_rg"),
    path("affectation_Ru/",views.affectation_Ru,name="liste_affectation"),
    path("alerter_ru/", views.envoyer_alert, name="envoyer_alert"),
    path("Dashboard_DUR/",views.dashboard_Dur,name="DUR"),
    path("affectation_DUR/",views.affectation_N3,name="affectation_N3"),
    path("validation_liste",views.liste_N1_non_valides_N3,name="liste_ru_non_valides_dur"),
    path("Dashboard_N4/",views.page_N4,name="page_N4"),
    path("affectation_N4/",views.affectation_N4,name="affectation_N4"),
    path("validation_N4/",views.validation_N4,name="validation_N4"),
    path("filter_date",views.validation_date,name="validation_date")
]