from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur ,Unite
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q,Count,Sum
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
import json
from django.db import transaction, IntegrityError
from django.views.decorators.csrf import ensure_csrf_cookie
from Collaborateur.views import rec , Ru_Rg, liste_Ru_par_Rg, Rg_Dur,liste_N1_pr_N3
from django.views.decorators.http import require_POST
from utilisateur.decorators import role_required
from django.utils.dateparse import parse_date

@ensure_csrf_cookie
#valider la liste des operateurs d'un Ru
def valider(request):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON invalide"}, status=400)

    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "Session expirée."}, status=401)

    liste_valides = data.get("valides", [])
    liste_refuses = data.get("changement",[])
    liste_depart =  data.get("depart", [])
    liste_ajouter = data.get("ajouter", [])
    try:
        with transaction.atomic():
            for operateur in liste_valides:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get((Q(it=operateur))), Ru_id=it,
                nature="V")
            for operateur in liste_refuses:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur["it"]),  Ru_id=it,
                nv_Ru=Collaborateur.objects.get(it=operateur["nvRu"]), nature="C"
            )
            for operateur in liste_depart:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur),  Ru_id=it, nature="D"
            )
            for operateur in liste_ajouter:
                declaration_effectif.objects.create(
                collaborateur_it=Collaborateur.objects.get(it=operateur), Ru_id=it,
                nature="A"
            )
    except (IntegrityError, KeyError, Collaborateur.DoesNotExist) as e:
        return JsonResponse({"error": "Erreur lors de l'enregistrement.", "status": "erreur"}, status=400)

    return JsonResponse({"status": "valider"})

#rederiger vers la page de validation
@role_required('N+1')
def validation_view(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    aujourdhui = timezone.localdate()

    if der and der.date == aujourdhui:
        operateurs_finaux = declaration_effectif.objects.filter(Ru_id=it, date=aujourdhui)
        status = "True"
    else:
        operateurs_finaux = rec(request)
        status = "False"

    nbr = operateurs_finaux.count()

    return render(
        request,
        'declaration_effectif/Validation.html',
        {"operateurs_finaux": operateurs_finaux, "nbr": nbr, "status": status, "date": aujourdhui}
    )

#return la valeur par syste et par reel
def difference(request):
    it = request.session.get("it")

    der = declaration_effectif.objects.filter(
        Ru_id=it
    ).order_by("-date").first()

    operateur_systeme = Collaborateur.objects.filter(ru_it=it)
    liste_s = set(operateur_systeme.values_list("it", flat=True))

    if der:
        operateur_reel = declaration_effectif.objects.filter(
            Ru_id=it,
            nature__in=["V", "A"],
            date=der.date
        )

        liste_r = set(
            operateur_reel.values_list("collaborateur_it__it", flat=True)
        )
        r=liste_r - liste_s
        s=liste_s - liste_r
        reel = Collaborateur.objects.filter(it__in=r)
        systeme = Collaborateur.objects.filter(it__in=s)

        return {
            "systeme1": systeme,
            "reel1": reel,
        }

    return {
        "systeme1":Collaborateur.objects.none() ,
        "reel1": Collaborateur.objects.none(),
    }

#rederiger ver page histo avec declaration ch
def histo(ut):
    # Si 'ut' est vide/None ou si le collaborateur n'existe pas
    if not ut:
        return {"declarations": [], "nbr": 0}

    resp = Collaborateur.objects.filter(it=ut).first()

    if not resp:
        return {"declarations": [], "nbr": 0}

    declarations = historique.objects.filter(
        Q(initial=resp.nom_complete) & ~Q(etat="Terminé")
    )
    nbr = declarations.count()

    return {"declarations": declarations, "nbr": nbr}

@role_required('N+1')
def histo_aff(request):
    util = request.session.get("it")
    changements = histo(util)

    return render(
        request,
        "declaration_effectif/Affectations_historique.html",
        {"info": changements["declarations"], "nbr": changements["nbr"]},
    )
#supp une declaration
def supprimer(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    if der:
        declaration_effectif.objects.filter(Ru_id=it, date=der.date).delete()
        return JsonResponse({"valide": True})
    return JsonResponse({"valide": False})

#afficher btn mod ds page validation
def afficher_modifier(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    aujourdhui = timezone.localdate()

    if der and der.date == aujourdhui:
        return JsonResponse({"valide": True})
    return JsonResponse({"valide": False})

#rederiger vers la page validation_rg+ liste de Ru pas valider
@role_required('N+2')
def liste_ru_non_valides_Rg(request):
    maint = timezone.localdate()
    it=request.session.get("it")
    liste_ru = Ru_Rg(it)
    liste_n1_ids=set(liste_ru.values_list("it",flat=True))

    # RU ayant fait une déclaration aujourd'hui
    declaration = declaration_effectif.objects.filter(date=maint, Ru_id__in=liste_n1_ids)
    liste_Ru = set(declaration.values_list("Ru_id", flat=True))

    # RU qui ont au moins un opérateur (collaborateur)
    liste_ru_avec_operateurs = set(
            Collaborateur.objects.filter(ru_it_id__in=liste_n1_ids)
            .values_list("ru_it_id", flat=True)
            .distinct())

    # RU non validés = ont des opérateurs MAIS pas de déclaration aujourd'hui
    ru_non_valides_ids = liste_ru_avec_operateurs - liste_Ru

    # On récupère les RU eux-mêmes (pas les collaborateurs)
    non_valides = liste_ru.filter(it__in=ru_non_valides_ids).distinct()

    return render(
        request,
        "declaration_effectif/RG/validation_rg.html",
        {"non_valides": non_valides,"date": maint},
    )

#liste des affectations des n+2
@role_required('N+2')
def affectation_Ru(request):
    
    util = request.session.get("it")

    changements = histo(util)
    toutes_declarations=[]
    n1 = Ru_Rg(util)
    toutes_declarations_N2 = list(changements["declarations"])
    total_nbr2 = changements["nbr"]
    total_nbr=0

    for n in n1:
        changements_n = histo(n.it)
        toutes_declarations.extend(changements_n["declarations"])
        total_nbr += changements_n["nbr"]

    return render(
        request,
        "declaration_effectif/RG/affectation.html",
        {    "n2":toutes_declarations_N2,
            "info": toutes_declarations,
            "nbr": total_nbr,
        },
    )


#rederiger vers la page validation_n+3 + liste de n+1 pas valider
@role_required('N+3')
def liste_N1_non_valides_N3(request):
    maint = timezone.localdate()
    it = request.session.get("it")
    n1=liste_N1_pr_N3(it)
    declaration = declaration_effectif.objects.filter(
        date=maint,
        Ru_id__in=n1,
    )
    liste_declares = set(declaration.values_list("Ru_id", flat=True))
    liste_ru_avec_operateurs = set(
            Collaborateur.objects.filter(ru_it_id__in=n1)
            .values_list("ru_it_id", flat=True)
            .distinct()
        )

    # RU n'ayant pas encore effectué leur déclaration
    non_valides = Collaborateur.objects.filter(
        it__in=liste_ru_avec_operateurs
    ).exclude(it__in=liste_declares)

    return render(
        request,
        "declaration_effectif/DUR/validation.html",
        {"non_valides": non_valides,"date": maint}
    )

#liste des affectations des Ru
@role_required('N+3')
def affectation_N3(request):
    util = request.session.get("it")

    def collecter(it, deja_vus=None):
        if deja_vus is None:
            deja_vus = set()
        if it in deja_vus:
            return
        deja_vus.add(it)

        changements_it = histo(it)
        toutes_declarations.extend(changements_it["declarations"])
        nonlocal total_nbr
        total_nbr += changements_it["nbr"]

        n0 = Ru_Rg(it)
        for e in n0:
            collecter(e.it, deja_vus)

    changements = histo(util)
    toutes_declarations_N2 = list(changements["declarations"])
    total_nbr2 = changements["nbr"]

    toutes_declarations = []
    total_nbr = 0

    n1 = Rg_Dur(util)
    for n in n1:
        collecter(n.it)

    return render(
        request,
        "declaration_effectif/DUR/affectation.html",
        {
            "n2": toutes_declarations_N2,
            "info": toutes_declarations,
            "nbr": total_nbr,
        },
    )

def envoyer_alert(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Requête invalide."}, status=400)

    ru_it = data.get("ru_it")
    it = request.session.get("it")

    if not ru_it:
        return JsonResponse({"status": "error", "message": "Matricule du RU manquant."}, status=400)
    if not it:
        return JsonResponse({"status": "error", "message": "Utilisateur non authentifié."}, status=401)

    emetteur_obj = get_object_or_404(Collaborateur, it=it)
    recepteur_obj = get_object_or_404(Collaborateur, it=ru_it)

    Alert.objects.create(
        emetteur=emetteur_obj,
        recepteur=recepteur_obj,
        contenu="Vous n'avez pas déclaré votre liste des effectifs pour aujourd'hui.",
        lu=0
    )

    return JsonResponse({"status": "ok", "message": "Alerte envoyée."})
@role_required('N+3')
def dashboard_Dur(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")

    it_n1 = liste_N1_pr_N3(it)
    collaborateurs_n1 = Collaborateur.objects.filter(it__in=it_n1)

    liste_ru_stats = []
    total_syst = 0
    total_r = 0
    maquette_totale = 0

    for collab in collaborateurs_n1:
        maint = declaration_effectif.objects.filter(Ru_id=collab.it).order_by("-date").first()
        systeme = Collaborateur.objects.filter(ru_it_id=collab.it).count()
        dec = declaration_effectif.objects.filter(date=maint, Ru_id=collab.it)
        reel = dec.count() if dec else systeme

        unite_abrev = collab.unite_id
        maquette = 0
        if unite_abrev:
            u = Unite.objects.filter(abreviation=unite_abrev).first()
            if u and u.maquette:
                maquette = u.maquette

        liste_ru_stats.append({"n1": collab, "reel": reel, "systeme": systeme, "maquette": maquette})
        total_r += reel
        total_syst += systeme

    unites_ab = set(collaborateurs_n1.values_list("unite_id", flat=True))
    if unites_ab:
        unites =set(Unite.objects.filter(abreviation__in=unites_ab).values_list("maquette",flat=True))
        for u in unites:
            maquette_totale += u

    return render(request, "declaration_effectif/DUR/dashboard.html", {
        "liste_ru_stats": liste_ru_stats,
        "total_r": total_r,
        "total_syst": total_syst,
        "maquette_totale": maquette_totale,"maint":maint if maint else timezone.localdate()
    })


def get_all_n1_under(it_parent):
    """
    Récupère TOUS les ITs des responsables N1 (ceux qui gèrent directement des opérateurs)
    situés sous 'it_parent', peu importe la profondeur hiérarchique.
    """
    n1_set = set()
    
    # Enfants directs du parent
    enfants_its = list(Collaborateur.objects.filter(ru_it_id=it_parent).values_list("it", flat=True))
    
    for enfant_it in enfants_its:
        # Est-ce que cet enfant a lui-même des subordonnés ?
        a_des_subordonnes = Collaborateur.objects.filter(ru_it_id=enfant_it).exists()
        
        if a_des_subordonnes:
            # On vérifie si ses subordonnés sont des "feuilles" (opérateurs) ou d'autres responsables
            # On descend récursivement
            sub_n1 = get_all_n1_under(enfant_it)
            
            if sub_n1:
                # Il a des N1 en dessous de lui (c'est donc un N2, N3...)
                n1_set.update(sub_n1)
            else:
                # Il n'a pas d'autres responsables sous lui, mais gère des opérateurs : c'est un N1 !
                n1_set.add(enfant_it)

    return n1_set

@role_required('N+4')
def page_N4(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")
        
    maint = timezone.localdate()

    # 1. Département(s) associés au N4
    dep = set(
        Collaborateur.objects.filter(it=it)
        .values_list("departement", flat=True)
    )

    # 2. Récupération de tous les ITs des N1 dépendant du N4 (arbre hiérarchique)
    n1_its = get_all_n1_under(it)

    # Si le N4 a lui-même des opérateurs en direct, on s'assure de l'inclure si besoin
    if not n1_its:
        # Vérification si le N4 gère directement des opérateurs sans intermédiaire
        if Collaborateur.objects.filter(ru_it=it).exists():
            n1_its.add(it)

    # 3. Récupération des objets Collaborateur N1 avec annotation de l'effectif système (opérateurs rattachés)
    # select_related('unite') évite de refaire des requêtes SQL pour la maquette
    liste_n1 = (
        Collaborateur.objects.filter(it__in=n1_its)
        .select_related("unite")
        .annotate(systeme_count=Count("subordonnes"))
    )

    # 4. Chargement en 1 seule requête des déclarations du jour pour les N1
    declarations_du_jour = dict(
        declaration_effectif.objects.filter(date=maint, Ru_id__in=n1_its)
        .values_list("Ru_id")
        .annotate(total=Count("id"))
    )

    liste_ru_stats = []
    total_syst = 0
    total_r = 0
    unites_traitees = set()
    maquette_totale = 0

    # 5. Construction de la liste des statistiques
    for n1 in liste_n1:
        collaborateur_it = n1.it

        # Effectif Système (nombre de subordonnés)
        systeme = n1.systeme_count

        # Effectif Réel (depuis les déclarations ou valeur par défaut = systeme)
        reel = declarations_du_jour.get(collaborateur_it, systeme)

        # Récupération de la maquette associée via l'Unite rattachée au N1
        maquette = 0
        if n1.unite:
            maquette = n1.unite.maquette or 0
            if n1.unite.abreviation not in unites_traitees:
                maquette_totale += maquette
                unites_traitees.add(n1.unite.abreviation)

        liste_ru_stats.append({
            "n1": n1,
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette,
        })

        total_r += reel
        total_syst += systeme

    # 6. Envoi des données au template dashboard.html
    return render(
        request,
        "declaration_effectif/N4/dashboard.html",
        {
            "liste_ru_stats": liste_ru_stats,
            "total_r": total_r,
            "total_syst": total_syst,
            "maquette_totale": maquette_totale,
            "dep": dep,"maint":maint if maint else timezone.localdate()
        },
    )

def get_n1_pour_niveau_orm(it_parent):
    # Niveau 1 sous le parent
    niv1 = set(Collaborateur.objects.filter(ru_it_id=it_parent).values_list("it", flat=True))
    
    # Niveau 2
    niv2 = set(Collaborateur.objects.filter(ru_it_id__in=niv1).values_list("it", flat=True))
    
    # Niveau 3
    niv3 = set(Collaborateur.objects.filter(ru_it_id__in=niv2).values_list("it", flat=True))

    # On ne garde QUE les ITs de ceux qui ont réellement des opérateurs sous leurs ordres
    n1_its = set(
        Collaborateur.objects.filter(ru_it_id__in=niv3)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    # Si le parent est LUI-MÊME un N1 direct (ses enfants sont des opérateurs)
    if not n1_its and niv1:
        return niv1

    return n1_its


def listes_de_N4(it_n4):
    collaborateurs = Collaborateur.objects.filter(ru_it_id=it_n4)
    liste_N1 = set()

    for c in collaborateurs:
        n1_its = get_n1_pour_niveau_orm(c.it)
        liste_N1.update(n1_its)

    
    n4_n1_directs = get_n1_pour_niveau_orm(it_n4)
    liste_N1.update(n4_n1_directs)

    return liste_N1


def get_tous_les_sous_responsables(it_parent):
    responsables = set()
    
    # 1. Récupération de tous les enfants directs du parent
    enfants_its = list(
        Collaborateur.objects.filter(ru_it=it_parent).values_list("it", flat=True)
    )

    for enfant_it in enfants_its:
        # Vérifie si cet enfant a des subordonnés (est-ce un responsable ?)
        subordonnes_its = list(
            Collaborateur.objects.filter(ru_it=enfant_it).values_list("it", flat=True)
        )

        if subordonnes_its:
            # C'est un responsable (N3, N2 ou N1), on l'ajoute
            responsables.add(enfant_it)

            # On vérifie si ses subordonnés ont à leur tour des personnes sous leurs ordres
            a_des_sous_responsables = Collaborateur.objects.filter(
                ru_it__in=subordonnes_its
            ).exists()

            if a_des_sous_responsables:
                # C'est un responsable de niveau supérieur (N3 / N2) :
                # On descend récursivement pour récupérer ses N2 et N1
                responsables.update(get_tous_les_sous_responsables(enfant_it))

    return responsables

@role_required('N+4')
def affectation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    # Récupération globale de TOUS les sous-responsables (N3, N2, N1)
    sous_responsables_its = get_tous_les_sous_responsables(util)

    toutes_declarations = []
    total_nbr = 0

    # Parcours des ITs de tous les responsables uniques
    for c_it in sous_responsables_its:
        changements_n = histo(c_it)
        toutes_declarations.extend(changements_n.get("declarations", []))
        total_nbr += changements_n.get("nbr", 0)

    # Historique propre au N4
    changements_n4 = histo(util)
    
    return render(
        request,
        "declaration_effectif/N4/affectation.html",
        {
            "toutes_declarations_N4": changements_n4.get("declarations", []),
            "toutes_declarations": toutes_declarations,
            "total_N4": changements_n4.get("nbr", 0),
            "total_nbr": total_nbr
        }
    )


@role_required('N+4')
def validation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    maint = timezone.localdate()

    # 1. Récupération récursive directe de TOUS les ITs des N1 sous ce N4
    # (ce sont les responsables qui gèrent directement les opérateurs)
    n1_its = get_all_n1_under(util)

    # Si le N4 gère directement des opérateurs sans manager intermédiaire
    if not n1_its:
        if Collaborateur.objects.filter(ru_it=util).exists():
            n1_its.add(util)

    # 2. Récupérer la liste des ITs des RU/N1 qui ONT DÉJÀ soumis leur déclaration aujourd'hui
    liste_declares = set(
        declaration_effectif.objects.filter(
            date=maint,
            Ru_id__in=n1_its
        ).values_list("Ru_id", flat=True)
    )

    # 3. Récupérer les collaborateurs N1 qui n'ont PAS ENCORE déclaré aujourd'hui
    # On utilise select_related pour précharger leur département / unité si vous l'affichez dans le template
    non_valides = Collaborateur.objects.filter(
        it__in=n1_its
    ).exclude(
        it__in=liste_declares
    ).select_related("departement", "unite")

    return render(
        request,
        "declaration_effectif/N4/validation.html",
        {
            "non_valides": non_valides,
            "total_non_valides": non_valides.count(),
        },
    )

def validation_date(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    # Conversion sécurisée de la chaîne en objet date Python
    query_date = parse_date(time_str) if time_str else None

    # Verification si la date sélectionnée est aujourd'hui
    # status = False si c'est aujourd'hui, True sinon
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    # 1. Récupération des RU gérés
    n1 = Ru_Rg(it)
    liste_n1 = set(n1.values_list("it", flat=True))

    # 2. Récupération des RU ayant déjà fait leur déclaration
    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(
                date=query_date, 
                Ru_id__in=liste_n1
            ).values_list("Ru_id", flat=True)
        )

    # 3. Soustraction entre les 2 sets Python (les IT non encore déclarés)
    liste_it_manquants = liste_n1 - declarations_faites

    # 4. Filtrage des collaborateurs correspondants
    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )

    # 5. Retour sous forme de réponse JSON propre
    return JsonResponse({
        "resultats": resultats,
        "status": status
    })