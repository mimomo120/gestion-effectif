from django.conf.locale import it
from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur ,Unite
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q,Count,Sum ,Max
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
import json
from datetime import date , datetime , timedelta
from django.db import transaction, IntegrityError
from django.views.decorators.csrf import ensure_csrf_cookie
from Collaborateur.views import rec , Ru_Rg, liste_N1_par_N2, Rg_Dur,liste_N1_pr_N3 ,liste_N3_N4 ,SystEff, reelEff
from django.views.decorators.http import require_POST
from utilisateur.decorators import role_required
from django.utils.dateparse import parse_date
from django.core.paginator import Paginator
import bisect
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache

@ensure_csrf_cookie

# ============================================================
# Valider la liste des operateurs
# ============================================================
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

# ============================================================
#fct pr rederiger vers la page validation + liste des operateurs
# ============================================================

@role_required('N+1')
def validation_view(request):
    it = request.session.get("it")
    if not it:
        return render(request, 'declaration_effectif/N1/Validation.html', {
            "operateurs_finaux": [], "nbr": 0, "status": False, "date": timezone.localdate()
        })

    # 1) calculer les directs du RU (objets Collaborateur)
    directs_qs = Collaborateur.objects.filter(ru_it__it=it).exclude(it=it)

    # ids des directs (PK)
    direct_ids = list(directs_qs.values_list('it', flat=True))

    # 2) parmi ces directs, lesquels sont référencés comme managers (ru_it_id) par d'autres ?
    # -> ids des directs qui sont managers
    manager_direct_ids = set(
        Collaborateur.objects.filter(ru_it_id__in=direct_ids)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )

    # 3) récupérer les "it" correspondants à ces directs-managers pour comparaison avec declaration_effectif.collaborateur_it_id
    manager_direct_its = set(
        Collaborateur.objects.filter(it__in=manager_direct_ids)
        .values_list('it', flat=True)
    )

    # 4) si il y a une déclaration aujourd'hui, prendre ses lignes puis exclure les managers
    aujourdhui = timezone.localdate()
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der and der.date == aujourdhui:
        # déclarations du jour pour ce RU
        operateurs_finaux_qs = declaration_effectif.objects.filter(Ru_id=it, date=aujourdhui)
        # exclure les entrées dont collaborateur_it_id correspond à un manager direct
        operateurs_finaux_qs = operateurs_finaux_qs.exclude(collaborateur_it_id__in=manager_direct_its)
        status = True
        operateurs_finaux = operateurs_finaux_qs
    else:
        candidats = rec(request)

        if hasattr(candidats, 'exclude'):
            operateurs_finaux = candidats.exclude(it__in=manager_direct_its)
        else:
            
            operateurs_finaux = [c for c in candidats if getattr(c, 'it', None) not in manager_direct_its]
        status = False
    if hasattr(operateurs_finaux, 'count'):
        nbr = operateurs_finaux.count()
    else:
        nbr = len(operateurs_finaux)

    return render(
        request,
        'declaration_effectif/N1/Validation.html',
        {
            "operateurs_finaux": operateurs_finaux,
            "nbr": nbr,
            "status": status,
            "date": aujourdhui
        }
    )

# ============================================================
# return ecart S/R et R/S
# ============================================================
def difference(request):
    it = request.session.get("it")

    der = declaration_effectif.objects.filter(
        Ru_id=it
    ).order_by("-date").first()

    operateur_systeme = SystEff(it)
    liste_s = set(operateur_systeme.values_list("it", flat=True))


    operateur_reel = reelEff(it)
    if  operateur_reel :
        liste_r = set(
            operateur_reel.values_list("it", flat=True)
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

# ============================================================
# recupere la liste des affectations d'un collaborateur
# ============================================================
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

# ============================================================
# rederiger vers la page des affectations avec  les affectations
# ============================================================
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
        "declaration_effectif/N1/Affectations_historique.html",
        context,
    )
# ============================================================
# Supprimer la declaration effectuers ds le jour meme
# ============================================================
def supprimer(request):
    it = request.session.get("it")

    if not it:
        return JsonResponse({"status": "erreur", "error": "Session invalide"}, status=401)

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der:
        declaration_effectif.objects.filter(Ru_id=it, date=der.date).delete()
        return JsonResponse({"status": "supprimer"})

    return JsonResponse({"status": "erreur", "error": "Aucune déclaration à supprimer"}, status=404)

# ============================================================
# rederiger vers la page des affectations avec  les affectations
# ============================================================
def afficher_modifier(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    aujourdhui = timezone.localdate()

    if der and der.date == aujourdhui:
        return JsonResponse({"valide": True})
    return JsonResponse({"valide": False})


# ============================================================
# rederiger vers la page des respo N+1 ss validation
# ============================================================
@role_required('N+2')
def liste_N1_non_valides_N2(request):
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
        "declaration_effectif/N2/validation.html",
        {"non_valides": non_valides,"date": maint},
    )


# ============================================================
# rederiger vers la page des affectations de N+2 et c'est N+1
# ============================================================
@role_required('N+2')
def affectation_N1(request):
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

    return render(request, "declaration_effectif/N2/affectation.html", context)

# ============================================================
# rederiger vers la page des respo N+1 ss validation pr N+3
# ============================================================
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
        "declaration_effectif/N3/validation.html",
        {"non_valides": non_valides,"date": maint}
    )
# ============================================================
# # rederiger vers la page des affectations de N+2 et c'est N+1 ,N+2
# ============================================================
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

    return render(request, "declaration_effectif/N2/affectation.html", context)
    
# ============================================================
# fct pr engregistrer les alerts envoier
# ============================================================
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

# ============================================================
# # rederiger vers dashboard de N+3
# ============================================================
@role_required('N+3')
def dashboard_N3 (request):
    it_session_original = request.session.get("it")
    if not it_session_original:
        return redirect("login")

    it_n1 = liste_N1_pr_N3(it_session_original)
    if not it_n1:
        return render(request, "declaration_effectif/N3/dashboard.html", {
            "liste_ru_stats": [], "total_r": 0, "total_syst": 0, "MR": 0, "MS": 0,
            "maquette_totale": 0, "maint": timezone.localdate(),
            "chart_labels_json": json.dumps([]), "chart_data_json": json.dumps([]), "non_valides": 0
        })

    today = timezone.now().date()
    start = today - timedelta(days=6)
    dates = [start + timedelta(days=i) for i in range(7)]
    labels_list = [d.strftime('%d %b') for d in dates]

    collaborateurs_n1 = Collaborateur.objects.filter(it__in=it_n1).select_related('unite')

    # 1) Calcul des stats par RU en utilisant SystEff et reelEff
    liste_ru_stats = []
    total_syst = 0
    total_r = 0
    maquette_totale = 0
    maint_global = None

    seen_unite_ids = set()

    try:
        for collab in collaborateurs_n1:
            ru_it = collab.it

            # Injection temporaire du RU dans la session pour exécuter vos 2 fonctions
            request.session["it"] = ru_it

            # Effectif système issu de SystEff(request)
            qs_syst = SystEff(collab.it)
            systeme = qs_syst.values('it').distinct().count()

            # Effectif réel issu de reelEff(request)
            qs_reel = reelEff(collab.it)
            reel = qs_reel.values('it').distinct().count()

            # Récupération de la dernière date de déclaration (A/V) pour ce RU
            last_decl = (
                declaration_effectif.objects
                .filter(Ru_id=ru_it, nature__in=["A", "V"])
                .order_by("-date")
                .first()
            )
            date_ref = last_decl.date if last_decl else None

            # --- calcul de la maquette (éviter les doublons par unité) ---
            maquette = 0
            unit_key = getattr(collab, "unite_id", None)
            if unit_key and unit_key not in seen_unite_ids:
                # collab.unite est select_related, mais on protège l'accès
                maquette = getattr(collab.unite, "maquette", 0) or 0
                seen_unite_ids.add(unit_key)

            liste_ru_stats.append({
                "n1": collab,
                "matricule": getattr(collab, "matricule", None),
                "nom_complete": getattr(collab, "nom_complete", ""),
                "unite": getattr(collab, 'unite_id', None),
                "reel": reel,
                "systeme": systeme,
                "maquette": getattr(collab.unite, "maquette", 0) or 0,
                "mr": reel - getattr(collab.unite, "maquette", 0) or 0,
                "ms": systeme - getattr(collab.unite, "maquette", 0) or 0,
                "last_decl_date": date_ref,
            })

            total_r += reel
            total_syst += systeme
            maquette_totale += maquette
            if date_ref and (maint_global is None or date_ref > maint_global):
                maint_global = date_ref

    finally:
        # Restauration systématique du RU original dans la session
        request.session["it"] = it_session_original

    if maint_global is None:
        maint_global = timezone.localdate()

    # 2) Calcul des RU non validés aujourd'hui
    liste_declares_today = set(
        declaration_effectif.objects
        .filter(date=today, Ru_id__in=it_n1)
        .values_list('Ru_id', flat=True)
    )
    non_valides = sum(1 for stat in liste_ru_stats if stat["systeme"] > 0 and stat["n1"].it not in liste_declares_today)

    # 3) Données de la session courante (N+3)
    operateurs_systeme_session = SystEff(it_session_original)
    syste_session = operateurs_systeme_session.values('it').distinct().count()

    # 4) Série temporelle pour le graphique sur 7 jours
    decl_window_qs = (
        declaration_effectif.objects
        .filter(Ru_id__in=it_n1, nature__in=["A", "V"], date__gte=start, date__lte=today)
        .values('Ru_id', 'date')
        .annotate(total=Count('collaborateur_it_id', distinct=True))
    )

    decls_by_ru = {}
    for r in decl_window_qs:
        decls_by_ru.setdefault(r['Ru_id'], []).append((r['date'], r['total']))
    for ru_key in decls_by_ru:
        decls_by_ru[ru_key].sort()

    data_totale_par_jour = [0] * len(dates)
    stat_dict = {stat["n1"].it: stat["systeme"] for stat in liste_ru_stats}

    for ru_it_key in [c.it for c in collaborateurs_n1]:
        ru_decls = decls_by_ru.get(ru_it_key, [])
        ru_system = stat_dict.get(ru_it_key, 0)
        ru_dates = [dt for dt, _ in ru_decls]
        ru_totals = [t for _, t in ru_decls]

        for idx, d in enumerate(dates):
            if ru_dates:
                pos = bisect.bisect_right(ru_dates, d) - 1
                if pos >= 0:
                    data_totale_par_jour[idx] += ru_totals[pos]
                    continue
            data_totale_par_jour[idx] += ru_system

    context = {
        "liste_ru_stats": liste_ru_stats,
        "total_r": total_r,
        "MR": total_r - maquette_totale,
        "MS": total_syst - maquette_totale,
        "total_syst": total_syst,
        "maquette_totale": maquette_totale,
        "maint": maint_global,
        "chart_labels_json": json.dumps(labels_list),
        "chart_data_json": json.dumps(data_totale_par_jour),
        "non_valides": non_valides,
        "operateurs_systeme_session": operateurs_systeme_session,
        "syste_session": syste_session,
    }
    return render(request, "declaration_effectif/N3/dashboard.html", context)
# ============================================================
# # rederiger vers dashboard de N+4
# ============================================================
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
        systeme1 =SystEff(n1.it).count()
        reel1=reelEff(n1.it).count()

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

# ============================================================
# # rederiger LISTE DES AFFECTATION DE N+4
# ============================================================
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
        "declaration_effectif/N2/affectation.html",
    {
        "n2": page_obj_n4,
        "page_obj_n2": page_obj_n4,
        "info": page_obj_n1,
        "page_obj_n1": page_obj_n1,
        "nbr2": total_N4,
        "nbr": total_nbr,
        "status": status,
    }
    )

# ============================================================
# # rederiger vers la page des N+1 sous N+4 non pas valider liste
# ============================================================
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
# ============================================================
# liste des validation filtre par date pr N+2
# ============================================================
def validation_date_N2(request):
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
# ============================================================
# liste des validation filtre par date pr N+3
# ============================================================
def validation_date_N3(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    # 1. Récupération des N+1 gérés
    liste_n1 = liste_N1_pr_N3(it)

    # 2. Récupération des RU ayant déjà fait leur déclaration
    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(
                date=query_date,
                Ru_id__in=liste_n1
            ).values_list("Ru_id", flat=True)
        )

    # 3. Soustraction entre les 2 sets Python
    liste_it_manquants = liste_n1 - declarations_faites

    # 4. Filtrage des collaborateurs correspondants
    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })


def get_badge_class(etat):
    """Retourne la classe CSS du badge selon le texte de l'état."""
    if not etat:
        return "badge-en-attente"
    etat_lower = str(etat).lower()
    if "valid" in etat_lower:
        return "badge-valide"
    elif "refus" in etat_lower:
        return "badge-refuse"
    elif "non démarr" in etat_lower or "non demarr" in etat_lower:
        return "badge-non-demarrer"
    else:
        return "badge-en-attente"

@role_required(['HRBP', 'ADMIN'])
def affectation_HRBP(request):
    it = request.session.get("it")
    status = request.GET.get("status", "all")
    dpt_filtre = request.GET.get("dpt", "all")
    role=request.session.get("role")
    if role == "HRBP" :
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "ADMIN":
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    departements = list(departements_qs.values_list("abreviation", flat=True))

    affectation = historique.objects.filter(
        Q(dpt_init__in=departements) | Q(dpt_acceuil__in=departements)
    ).exclude(etat="Terminé")

    # Filtrage fait en base (queryset), pas en Python sur une liste déjà chargée,
    # pour que la pagination reste efficace.
    if status == "valide":
        affectation = affectation.filter(etat__icontains="valid")
    elif status == "refuse":
        affectation = affectation.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        affectation = affectation.filter(etat__icontains="non démarr")

    # Filtrage par département (initial ou accueil) parmi le périmètre du HRBP
    if dpt_filtre != "all" and dpt_filtre in departements:
        affectation = affectation.filter(
            Q(dpt_init__abreviation=dpt_filtre) | Q(dpt_acceuil__abreviation=dpt_filtre)
        )

    # --- Pagination ---
    paginator = Paginator(affectation, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # --- Ajout de la classe badge calculée uniquement pour les lignes de la page affichée ---
    for a in page_obj:
        a.badge_class = get_badge_class(a.etat)

    return render(request, "declaration_effectif/HRBP/affectation.html", {
        "info": page_obj,
        "page_obj": page_obj,
        "status": status,
        "dpt_filtre": dpt_filtre,
        "departements": departements,
    })


@never_cache
@role_required('HRBP')
def responsables_ru_sans_declaration_du_jour(request):
    today = timezone.localdate()
    it = request.session.get("it")

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = departements_qs.values_list("abreviation", flat=True)
    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)
    responsable = list(
        collaborateurs_base
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=responsable)
    
    ru_ids = list(
        operateur
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=today)
        .values_list("Ru_id", flat=True)
        .distinct()
    )

    ru_ids_sans_declaration = [
        ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration
    ]

    ru = Collaborateur.objects.filter(it__in=ru_ids_sans_declaration)

    # --- Pagination ---
    paginator = Paginator(ru, 20)  # 20 RU par page, ajustable
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "declaration_effectif/HRBP/declaration.html", {
        "ru": page_obj,             # objet paginé, itérable dans le template comme avant
        "non_valides": ru,          # total réel (non paginé) pour le KPI "Non Validés"
        "page_obj": page_obj,       # pour les contrôles de pagination dans le template
        "departements": departements_qs,  # <-- ajouté : queryset d'objets Departement pour le <select>
        "date": today,
    })


def filter_date2(request):
    it = request.session.get("it")
    date_str = request.GET.get("time")
    dept = request.GET.get("dept", "").strip()

    if not date_str:
        return JsonResponse({"resultats": [], "status": False})

    try:
        date_selectionnee = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"resultats": [], "status": False})
    
    is_today = (date_selectionnee == timezone.localdate()) if date_selectionnee else False
    status = not is_today

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = list(departements_qs.values_list("abreviation", flat=True))

    # Si un département précis est demandé, on restreint dessus
    if dept:
        departements = [d for d in departements if d == dept]

    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)

    responsable = list(
        collaborateurs_base
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=responsable)

    ru_ids = list(
        operateur
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    # Déclarations faites à LA DATE SÉLECTIONNÉE (pas today)
    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=date_selectionnee)
        .values_list("Ru_id", flat=True)
        .distinct()
    )

    ru_ids_sans_declaration = [
        ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration
    ]

    ru_qs = Collaborateur.objects.filter(
        it__in=ru_ids_sans_declaration
    ).select_related("departement")

    resultats = [
        {
            "matricule": r.matricule,
            "it": r.it,
            "nom_complete": r.nom_complete,
            "lot": r.lot,
            "departement": {
                "abreviation": r.departement.abreviation if r.departement else ""
            },
        }
        for r in ru_qs
    ]

    return JsonResponse({
        "resultats": resultats,
        "status": status
    })

# ============================================================
# liste des validation filtre par date pr N+4
# ============================================================
def validation_date_N4(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    # Conversion sécurisée de la chaîne en objet date Python
    query_date = parse_date(time_str) if time_str else None

    # Vérification si la date sélectionnée est aujourd'hui
    # status = False si c'est aujourd'hui, True sinon
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    # 1. Récupération des N+1 gérés (via les N+3 sous ce N+4)
    liste = []
    liste_N3 = liste_N3_N4(it)
    for n in liste_N3:
        liste.extend(liste_N1_pr_N3(n))

    liste_n1 = set(liste)

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