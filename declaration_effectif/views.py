from django.conf.locale import it
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
from datetime import date , datetime , timedelta
from django.db import transaction, IntegrityError
from django.views.decorators.csrf import ensure_csrf_cookie
from Collaborateur.views import rec , Ru_Rg, liste_Ru_par_Rg, Rg_Dur,liste_N1_pr_N3 ,liste_N3_N4
from django.views.decorators.http import require_POST
from utilisateur.decorators import role_required
from django.utils.dateparse import parse_date
from django.core.paginator import Paginator

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
        "systeme1":Collaborateur.objects.none(),
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
    declarations = changements["declarations"]  # QuerySet

    status = request.GET.get("status", "all")

    if status == "valide":
        declarations = declarations.filter(etat__icontains="valid")
    elif status == "refuse":
        declarations = declarations.filter(etat__icontains="refus")

    paginator = Paginator(declarations, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context = {
        "info": page_obj,
        "page_obj": page_obj,
        "nbr": paginator.count,
        "status": status,
    }
    return render(
        request,
        "declaration_effectif/Affectations_historique.html",
        context,
    )
#supp une declaration
def supprimer(request):
    it = request.session.get("it")

    if not it:
        return JsonResponse({"status": "erreur", "error": "Session invalide"}, status=401)

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der:
        declaration_effectif.objects.filter(Ru_id=it, date=der.date).delete()
        return JsonResponse({"status": "supprimer"})

    return JsonResponse({"status": "erreur", "error": "Aucune déclaration à supprimer"}, status=404)

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
    
    # Récupération du paramètre status depuis la requête GET
    status = request.GET.get("status", "all")

    # 1. Chargement des données brutes N+2
    changements = histo(util)
    toutes_declarations_N2 = list(changements["declarations"])

    # 2. Chargement des données brutes N+1
    toutes_declarations = []
    n1 = Ru_Rg(util)

    for n in n1:
        changements_n = histo(n.it)
        toutes_declarations.extend(changements_n["declarations"])

    # Appliquer le filtrage par statut sur les listes Python
    if status == "valide":
        toutes_declarations_N2 = [
            d for d in toutes_declarations_N2 
            if d.etat and "valid" in str((d.etat)).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations 
            if d.etat and "valid" in str((d.etat)).lower()
        ]
    elif status == "refuse":
        toutes_declarations_N2 = [
            d for d in toutes_declarations_N2 
            if d.etat and "refus" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations 
            if d.etat and "refus" in str((d.etat)).lower()
        ]

    # Recalcul des totaux après filtrage
    total_nbr2 = len(toutes_declarations_N2)
    total_nbr = len(toutes_declarations)

    # 3. Pagination pour l'onglet N+1 ("info")
    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    # 4. Pagination pour l'onglet N+2 ("n2")
    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_n2 = request.GET.get("page_n2", 1)
    page_obj_n2 = paginator_n2.get_page(page_n2)

    context = {
        "n2": page_obj_n2,
        "page_obj_n2": page_obj_n2,
        "info": page_obj_n1,
        "page_obj_n1": page_obj_n1,
        "nbr": total_nbr,
        "nbr2": total_nbr2,
        "status": status,  # Transmis au template pour maintenir l'option sélectionnée
    }

    return render(request, "declaration_effectif/RG/affectation.html", context)


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

    # Récupération du paramètre status depuis la requête GET
    status = request.GET.get("status", "all")

    def collecter(it, deja_vus=None):
        if deja_vus is None:
            deja_vus = set()
        if it in deja_vus:
            return
        deja_vus.add(it)

        changements_it = histo(it)
        toutes_declarations.extend(changements_it["declarations"])

        n0 = Ru_Rg(it)
        for e in n0:
            collecter(e.it, deja_vus)

    # 1. Chargement des données brutes N+2
    changements = histo(util)
    toutes_declarations_N2 = list(changements["declarations"])

    # 2. Chargement des données brutes N+1 (et en dessous, récursivement)
    toutes_declarations = []
    n1 = Rg_Dur(util)
    for n in n1:
        collecter(n.it)

    # Appliquer le filtrage par statut sur les listes Python
    if status == "valide":
        toutes_declarations_N2 = [
            d for d in toutes_declarations_N2
            if d.etat and "valid" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations
            if d.etat and "valid" in str(d.etat).lower()
        ]
    elif status == "refuse":
        toutes_declarations_N2 = [
            d for d in toutes_declarations_N2
            if d.etat and "refus" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations
            if d.etat and "refus" in str(d.etat).lower()
        ]
    elif status == "non_demarrer":
        toutes_declarations_N2 = [
        d for d in toutes_declarations_N2
        if d.etat and "non démarr" in str(d.etat).lower()
    ]
        toutes_declarations = [
        d for d in toutes_declarations
        if d.etat and "non démarr" in str(d.etat).lower()
    ]

    # Recalcul des totaux après filtrage
    total_nbr2 = len(toutes_declarations_N2)
    total_nbr = len(toutes_declarations)

    # 3. Pagination pour l'onglet N+1 ("info")
    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    # 4. Pagination pour l'onglet N+2 ("n2")
    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_n2 = request.GET.get("page_n2", 1)
    page_obj_n2 = paginator_n2.get_page(page_n2)

    context = {
        "n2": page_obj_n2,
        "page_obj_n2": page_obj_n2,
        "info": page_obj_n1,
        "page_obj_n1": page_obj_n1,
        "nbr": total_nbr,
        "nbr2": total_nbr2,
        "status": status,
    }

    return render(request, "declaration_effectif/DUR/affectation.html", context)
    

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
    maint = timezone.localdate()
    collaborateurs_n1 = Collaborateur.objects.filter(it__in=it_n1)
    declaration = declaration_effectif.objects.filter(
            date=maint, Ru_id__in=it_n1
        )
    liste_declares = set(declaration.values_list("Ru_id", flat=True))
    
    liste_ru_avec_operateurs = set(
            Collaborateur.objects.filter(
                ru_it_id__in=it_n1
            )
            .values_list("ru_it_id", flat=True)
            .distinct()
        )
    non_valides = (
            Collaborateur.objects.filter(it__in=liste_ru_avec_operateurs)
            .exclude(it__in=liste_declares)
            .count()
        )
    liste_ru_stats = []
    total_syst = 0
    total_r = 0
    maquette_totale = 0
    maint = timezone.localdate()

    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]

    data_totale_par_jour = [0] * len(dates)

    for collab in collaborateurs_n1:
        maint_obj = declaration_effectif.objects.filter(Ru_id=collab.it).order_by("-date").first()
        date_ref = maint_obj.date if maint_obj else timezone.localdate()

        systeme = Collaborateur.objects.filter(ru_it_id=collab.it).count()

        dec = declaration_effectif.objects.filter(
            date=date_ref,
            Ru_id=collab.it,
            nature__in=["A", "V"]
        )
        reel = dec.count() if dec.exists() else systeme

        unite_abrev = collab.unite_id
        maquette = 0
        if unite_abrev:
            u = Unite.objects.filter(abreviation=unite_abrev).first()
            if u and u.maquette:
                maquette = u.maquette

        liste_ru_stats.append({
            "n1": collab,
            "matricule": collab.matricule,
            "nom_complete": collab.nom_complete,
            "unite": unite_abrev,
            "reel": reel,
            "systeme": systeme,
            "maquette": maquette,
            "mr": reel - maquette,
            "ms": systeme - maquette,
        })

        total_r += reel
        total_syst += systeme
        if maint_obj:
            maint = date_ref

        for i, d in enumerate(dates):
            derniere = declaration_effectif.objects.filter(
                Ru_id=collab.it, date__lte=d
            ).order_by("-date").first()

            if derniere:
                count = declaration_effectif.objects.filter(
                    Ru_id=collab.it,
                    date=derniere.date,
                    nature__in=["A", "V"]
                ).count()
            else:
                count = systeme

            data_totale_par_jour[i] += count

    unites_ab = set(collaborateurs_n1.values_list("unite_id", flat=True))
    if unites_ab:
        maquettes = Unite.objects.filter(abreviation__in=unites_ab).values_list("maquette", flat=True)
        maquette_totale = sum(m or 0 for m in maquettes)

    return render(request, "declaration_effectif/DUR/dashboard.html", {
        "liste_ru_stats": liste_ru_stats,
        "total_r": total_r,
        "MR": total_r - maquette_totale,
        "MS": total_syst - maquette_totale,
        "total_syst": total_syst,
        "maquette_totale": maquette_totale,
        "maint": maint,
        "chart_labels_json": json.dumps(labels_list),
        "chart_data_json": json.dumps(data_totale_par_jour),"non_valides":non_valides
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

    dep = set(
        Collaborateur.objects.filter(it=it)
        .values_list("departement", flat=True)
    )
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]
    liste = []
    reel = 0
    systeme = 0
    liste_ru_stats = []
    maquette = 0

    liste_N3 = liste_N3_N4(it)
    for n in liste_N3:
        liste.extend(liste_N1_pr_N3(n))

    liste_n1 = Collaborateur.objects.filter(it__in=liste)
    data_totale_par_jour = [0] * len(dates)

    for n1 in liste_n1:
        systeme1 = Collaborateur.objects.filter(ru_it_id=n1.it).count()
        der = declaration_effectif.objects.filter(Ru_id=n1.it).order_by("-date").first()
        if der:
            reel1 = declaration_effectif.objects.filter(
                Ru_id=n1.it, date=der.date, nature__in=["A", "V"]
            ).count()
        else:
            reel1 = systeme1

        maquette1 = 0
        if n1.unite_id:
            u = Unite.objects.filter(abreviation=n1.unite_id).first()
            if u and u.maquette:
                maquette1 = u.maquette

        for i, d in enumerate(dates):
            derniere = declaration_effectif.objects.filter(
                Ru_id=n1.it, date__lte=d
            ).order_by("-date").first()

            if derniere:
                count = declaration_effectif.objects.filter(
                    Ru_id=n1.it,
                    date=derniere.date,
                    nature__in=["A", "V"]
                ).count()
            else:
                count = systeme1   # <-- fixé (était "systeme")

            data_totale_par_jour[i] += count

        reel += reel1
        systeme += systeme1
        maquette += maquette1

        liste_ru_stats.append({
            "n1": n1,
            "reel1": reel1,
            "systeme1": systeme1,
            "maquette1": maquette1,
            "mr": reel1 - maquette1,
            "ms": systeme1 - maquette1,
        })

    # ---- Sorti de la boucle : calculé une seule fois ----
    declaration = declaration_effectif.objects.filter(
        date=maint, Ru_id__in=liste_n1.values_list("it", flat=True)
    )
    liste_declares = set(declaration.values_list("Ru_id", flat=True))

    liste_ru_avec_operateurs = set(
        Collaborateur.objects.filter(
            ru_it_id__in=liste_n1.values_list("it", flat=True)
        )
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    non_valides = (
        Collaborateur.objects.filter(it__in=liste_ru_avec_operateurs)
        .exclude(it__in=liste_declares)
        .count()
    )

    return render(request, "declaration_effectif/N4/dashboard.html", {
        "liste_ru_stats": liste_ru_stats,
        "reel": reel,
        "systeme": systeme,
        "maint": maint,
        "maquette": maquette,
        "MS": systeme - maquette,
        "MR": reel - maquette,
        "chart_labels_json": json.dumps(labels_list),
        "chart_data_json": json.dumps(data_totale_par_jour),
        "non_valides": non_valides,
    })

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



@role_required('N+4')
@role_required('N+4')
@role_required('N+4')
def affectation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    status = request.GET.get("status", "all")

    liste_N3 = liste_N3_N4(util)
    liste = []
    for n3 in liste_N3:
        liste.extend(Rg_Dur(n3))
    liste.extend(liste_N1_pr_N3(util))

    # Historique propre au N4
    changements_n4 = histo(util)
    toutes_declarations_N4 = list(changements_n4.get("declarations", []))

    # Historique cumulé de tous les niveaux sous ce N+4
    toutes_declarations = []
    for n in liste:
        changements_n = histo(n)
        toutes_declarations.extend(changements_n.get("declarations", []))

    if status == "valide":
        toutes_declarations_N4 = [
            d for d in toutes_declarations_N4
            if d.etat and "valid" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations
            if d.etat and "valid" in str(d.etat).lower()
        ]
    elif status == "refuse":
        toutes_declarations_N4 = [
            d for d in toutes_declarations_N4
            if d.etat and "refus" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations
            if d.etat and "refus" in str(d.etat).lower()
        ]
    elif status == "non_demarrer":
        toutes_declarations_N4 = [
            d for d in toutes_declarations_N4
            if d.etat and "non démarr" in str(d.etat).lower()
        ]
        toutes_declarations = [
            d for d in toutes_declarations
            if d.etat and "non démarr" in str(d.etat).lower()
        ]

    total_N4 = len(toutes_declarations_N4)
    total_nbr = len(toutes_declarations)

    # ---- Pagination pour les 2 listes ----
    paginator_n4 = Paginator(toutes_declarations_N4, 10)
    page_n4 = request.GET.get("page_n4", 1)
    page_obj_n4 = paginator_n4.get_page(page_n4)

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_n1 = request.GET.get("page_n1", 1)
    page_obj_n1 = paginator_n1.get_page(page_n1)

    return render(
        request,
        "declaration_effectif/N4/affectation.html",
        {
            "toutes_declarations_N4": page_obj_n4,
            "page_obj_n4": page_obj_n4,
            "toutes_declarations": page_obj_n1,
            "page_obj_n1": page_obj_n1,
            "total_N4": total_N4,
            "total_nbr": total_nbr,
            "status": status,
        }
    )


@role_required('N+4')
def validation_N4(request):
    util = request.session.get("it")

    if not util:
        return redirect("login")

    maint = timezone.localdate()
    liste=[]
    liste_N3 = liste_N3_N4(util)
    for n in liste_N3:
        liste.extend(liste_N1_pr_N3(n))

    liste_n1 = Collaborateur.objects.filter(it__in=liste)
    n1_its=set(liste_n1.values_list("it", flat=True))
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