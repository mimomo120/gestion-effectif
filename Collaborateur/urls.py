from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from  Collaborateur import views

urlpatterns = [
            path("",views.operateurs,name='operateurs'),
            path('rechercher/',views.filter_tableau ,name='chercher'),
            path("filter/",views.liste_par_jour,name="liste_par_jour"),
            path("operateur/",views.operateur,name="operateur"),
            path("operateurs/",views.operateurs,name="operateurs"),
            path("liste_Ru_par_Rg/",views.liste_Ru_par_Rg,name="RG"),
            path("liste_Rg_par_DUR/",views.liste_Rg_par_dur,name="liste_Rg_par_dur"),
            path("liste_N3/",views.respo_N4,name="liste_N3"),
            ]