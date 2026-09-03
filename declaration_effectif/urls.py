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
    path("validation_N2/",views.liste_N1_non_valides_N2,name="liste_N1_non_valides_N2"),
    path("affectation_N1/",views.affectation_N1,name="affectation_N2"),
    path("alerter_ru/", views.envoyer_alert, name="envoyer_alert"),
    path("Dashboard_N3/",views.dashboard_N3,name="Dashboard_N3"),
    path("affectation_N3/",views.affectation_N3,name="affectation_N3"),
    path("validation_liste",views.liste_N1_non_valides_N3,name="liste_N1_non_valides_N3"),
    path("Dashboard_N4/",views.page_N4,name="page_N4"),
    path("affectation_N4/",views.affectation_N4,name="affectation_N4"),
    path("validation_N4/",views.validation_N4,name="validation_N4"),
    path("filter_date/",views.validation_date_N2,name="validation_date"),
    path("filter_date_N3/",views.validation_date_N3,name="validation_date_N3"),
    path("affectation departements/",views.affectation_HRBP,name="affectation_HRBP"),
    path("declaration departements/",views.responsables_ru_sans_declaration_du_jour,name="declaration_HRBP"),
    path("filter_date2/",views.filter_date2,name="validation_filtre"),
    path('filter_date_N4', views.validation_date_N4, name='filter_date_N4'),
    path("PILOT/affectation",views.changement_dpt,name="affectation_PILOT"),
    
]