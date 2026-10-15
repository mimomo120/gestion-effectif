from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur , LoginLog
from Collaborateur.models import  Departement , Collaborateur ,Equipe, MaquetteN1
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q ,Count ,Max , F
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif ,Alert ,historique
from django.http import JsonResponse
from datetime import date
from declaration_effectif.views import difference , histo_aff ,determiner_hierarchie, calculer_niveaux_hierarchie
from Collaborateur.views import (
    rec, Ru_Rg, liste_N1_pr_N3, Rg_Dur, reelEff, SystEff,
    get_effectif_reel_ids, get_effectif_reel_ids_multi, get_managers_its, get_maquettes_n1_map,
    repartir_maquette_par_lot, LOT_VERS_CHAMP_MAQUETTE,
    _dates_fin_de_mois, _regrouper_par_annee, get_maquettes_a_date,
    get_managers_racines, calculer_maquette_totale_perimetre,
    get_tous_les_it_sous, get_tous_les_it_sous_reel,
    get_tous_les_it_sous_reel_evolution,reelEff_evolution_multi
)
from django.db.models import Sum ,F , OuterRef, Subquery
from .decorators import role_required
from datetime import timedelta
import calendar
import json
import bisect
from .forms import RegisterForm
from django.db import transaction, IntegrityError
from collections import Counter
from django.http import HttpResponseForbidden
import secrets
from collections import defaultdict
import string
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from django.utils.dateparse import parse_date

def login_view(request):
    if request.method == "POST":
        it = request.POST.get("it")
        password = request.POST.get("password")
        try:
            utilis = utilisateur.objects.get(pk=it)
            collab = utilis.it
            if check_password(password, utilis.password):
                request.session["it"] = utilis.it.it
                request.session["role"] = utilis.role
                request.session["nom"] = collab.nom_complete
                roles_disponibles = []

                if utilis.N1 :
                    roles_disponibles.append("N+1")
                if utilis.N2 :
                    roles_disponibles.append("N+2")
                if utilis.N3 :
                    roles_disponibles.append("N+3")
                if utilis.N4 :
                    roles_disponibles.append("N+4")
                if utilis.SUPER :
                    roles_disponibles.append("SUPER")
                if utilis.ADMIN :
                    roles_disponibles.append("ADMIN")
                if utilis.HRBP :
                    roles_disponibles.append("HRBP")
                if utilis.DRH :
                    roles_disponibles.append("DRH")
                if utilis.PILOT :
                    roles_disponibles.append("PILOT")
                
                request.session['roles_disponibles'] = roles_disponibles
                LoginLog.objects.create(
                    utilisateur=utilis,
                    action="LOGIN",
                    ip_address=request.META.get("REMOTE_ADDR")
                )
                if utilis.role == "N+1":
                    return redirect('dashboard_N1')

                elif utilis.role == "N+2":
                    return redirect("dashboard_N2")
                elif utilis.role == "N+3":
                    return redirect('Dashboard_N3')

                elif utilis.role == "N+4":
                    return redirect('page_N4')

                elif utilis.role == "SUPER":
                    return redirect('SUPER')
                elif utilis.role == "HRBP":
                    return redirect('dashboard')
                elif utilis.role == "DRH":
                    return redirect('dashboard')
                elif utilis.role == "ADMIN":
                    return redirect('dashboard')
                elif utilis.role == "PILOT":
                    return redirect('pilot')
            else:
                LoginLog.objects.create(
                    utilisateur=utilis,
                    action="FAILED",
                    ip_address=request.META.get("REMOTE_ADDR")
                )
                return render(
                    request,
                    "utilisateur/login.html",
                    {"message": "Mot de passe incorrect"}
                )
        except utilisateur.DoesNotExist:
            return render(
                request,
                "utilisateur/login.html",
                {"message": "Identifiant introuvable"}
            )
    else:
        return render(
                request,
                "utilisateur/login.html")


def register_view(request):
    if request.method == "POST":
        it = request.POST.get('it')
        password = request.POST.get('password')

        if not it or not password:
            return render(request, "utilisateur/register.html", {
                'message': "Vous devez remplir tous les champs."
            })

        try:
            col = Collaborateur.objects.get(it=it)
        except Collaborateur.DoesNotExist:
            return render(request, "utilisateur/register.html", {
                'message': "Ce collaborateur n'existe pas dans la base de données."
            })

        if utilisateur.objects.filter(it=col).exists():
            return render(request, "utilisateur/register.html", {
                'message': "Cet utilisateur est déjà enregistré."
            })

        role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it)

        if not role_calcule:
            return render(request, "utilisateur/accesInterdi.html")

        util, created = utilisateur.objects.update_or_create(
            it=col,
            defaults={
                "role": role_calcule,
                "password": make_password(password),
                "N1": n1,
                "N2": n2,
                "N3": n3,
                "N4": n4,
                "ADMIN": 0,
                "HRBP": 0,
                "SUPER": 0,"DRH":0,"PILOT":0
            }
        )

        return render(request, "utilisateur/login.html")

    return render(request, "utilisateur/register.html")


@role_required('N+1')
def tableau(request):
    it = request.session.get("it")
    if not it:
        return HttpResponseForbidden("Session expirée, veuillez vous reconnecter.")

    try:
        ru = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return HttpResponseForbidden("Collaborateur introuvable.")

    managers_its = get_managers_its()

    # ---- GESTION DE LA REQUÊTE AJAX POUR LA MODALE (HTML GÉNÉRÉ EN PYTHON AVEC ICÔNES) ----
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.GET.get('load_modal') == '1':
        date_debut = request.GET.get('date_debut')
        date_fin = request.GET.get('date_fin')
        
        mouvements_qs = declaration_effectif.objects.filter(Ru_id=it)
        if date_debut:
            mouvements_qs = mouvements_qs.filter(date__gte=date_debut)
        if date_fin:
            mouvements_qs = mouvements_qs.filter(date__lte=date_fin)

        mouvements_list = list(mouvements_qs.order_by("-date"))
        collab_ids = {d.collaborateur_it_id for d in mouvements_list}
        collab_map = {
            c.it: c for c in Collaborateur.objects.filter(it__in=collab_ids)
        }

        html_rows = ""
        mouvements_trouves = False

        for d in mouvements_list:
            mouvements_trouves = True
            collab = collab_map.get(d.collaborateur_it_id)
            nom_collab = collab.nom_complete if collab else "-"
            nature_disp = d.get_nature_display() if hasattr(d, 'get_nature_display') else str(d.nature)
            
            # Normalisation pour comparaison insensible à la casse/espaces
            nature_val = str(d.nature).strip().lower()
            
            nature_val = str(nature_val).strip().lower()

            if nature_val in ['a', 'v', 'validation', 'valider']:
                badge_class = "badge-entree"
                icon_class = "fas fa-check-circle"

            elif nature_val in ['d', 'depart', 'sortie']:
                badge_class = "badge-sortie"
                icon_class = "fas fa-user-minus"

            elif nature_val in ['c', 'changement', 'transfert']:
                badge_class = "badge-transfert"
                icon_class = "fas fa-exchange-alt"

            else:
                badge_class = "badge-transfert"
                icon_class = "fas fa-random"

            detail_texte = f"Vers {d.nv_Ru_id}" if nature_val in ['c', 'transfert'] and getattr(d, 'nv_Ru_id', None) else "Enregistré"

            html_rows += f"""
            <tr>
                <td class="text-nowrap text-muted"><i class="far fa-clock me-1"></i>{d.date}</td>
                <td class="fw-bold">{nom_collab}</td>
                <td><code>{d.collaborateur_it_id}</code></td>
                <td>
                    <span class="badge-mouvement {badge_class}">
                        <i class="{icon_class} me-1"></i>{nature_disp}
                    </span>
                </td>
                <td class="text-muted small">{detail_texte}</td>
            </tr>
            """
            
        if not mouvements_trouves:
            html_rows = '<tr><td colspan="5" class="empty-row text-center py-3">Aucun mouvement trouvé pour cette période.</td></tr>'
            
        return JsonResponse({'html': html_rows})

    # ---- CALCULS HABITUELS DU TABLEAU DE BORD ----
    operateurs_systeme = SystEff(it, managers_its=managers_its)
    syste = operateurs_systeme.values('it').distinct().count()

    operateurs_reel = reelEff(it, managers_its=managers_its)
    counts_qs = operateurs_reel.values('lot').annotate(total=Count('it', distinct=True))
    counts_par_lot = {r['lot']: r['total'] for r in counts_qs}
    counts_ls = operateurs_systeme.values('lot').annotate(total=Count('it', distinct=True))
    counts_lot_ls = {r['lot']: r['total'] for r in counts_ls}
    reel = operateurs_reel.values('it').distinct().count()
    diff = difference(request)
    systeme = diff.get("systeme1", 0)
    reel1 = diff.get("reel1", 0)

    maquette = 0
    A = E = P = C = 0
    maquette_n1 = MaquetteN1.objects.filter(n1_id=it, actif=True).first()
    if maquette_n1:
        A = maquette_n1.A
        P = maquette_n1.P
        C = maquette_n1.C
        E = maquette_n1.T
        maquette = maquette_n1.total

    Pr = counts_par_lot.get("P", 0)
    Ar = counts_par_lot.get("A", 0)
    OLr = counts_par_lot.get("O", 0)
    Er = counts_par_lot.get("E", 0)
    Cr = counts_par_lot.get("C", 0)

    Ps = counts_lot_ls.get("P", 0)
    As = counts_lot_ls.get("A", 0)
    OLs = counts_lot_ls.get("O", 0)
    Es = counts_lot_ls.get("E", 0)
    Cs = counts_lot_ls.get("C", 0)

    diff_r_m = reel - maquette
    diff_s_m = syste - maquette

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    date_declaration = der.date if der else timezone.localdate()
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime('%d %b') for d in dates]

    data_list = [
        reelEff(it, date_reference=d, managers_its=managers_its).values('it').distinct().count()
        for d in dates
    ]

    derniers_mouvements = derniers_mouvements_respo(it, limite=10)

    context = {
        'chart_labels': json.dumps(labels_list),
        'chart_data': json.dumps(data_list),
        'operateurs_systeme': operateurs_systeme,
        'syste': syste,
        'reel': reel,
        'diff_r_m': diff_r_m,
        'diff_s_m': diff_s_m,
        'date': date_declaration,
        'reel1': reel1,
        'systeme1': systeme,
        'Pr': Pr, 'Ar': Ar + OLr, 'Er': Er, 'Cr': Cr,
        'A': A, 'C': C, 'P': P, 'E': E,
        'maquette': maquette,
        'reel_par_lot_json': json.dumps([Ar + OLr, Pr, Er, Cr]),
        'maquette_par_lot_json': json.dumps([A, P, E, C]),
        'systeme_par_lot_json': json.dumps([As + OLs, Ps, Es, Cs]),
        'derniers_mouvements': derniers_mouvements,
    }
    return render(request, "declaration_effectif/N1/dashboard_N1.html", context)

def est_n1_pur(it_val):
    role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it_val)
    if role_calcule != "N+1":
        return False

    util = utilisateur.objects.filter(it_id=it_val).first()
    if not util:
        return False

    if util.role != "N+1":
        return False
    if util.N2 or util.N3 or util.N4:
        return False

    return True


def lister_n1_purs():
    _, l1, _, _, _ = calculer_niveaux_hierarchie()

    return utilisateur.objects.filter(
        it_id__in=l1,
        role="N+1",
        N2=0,
        N3=0,
        N4=0,
    )


def verifier(request):
    it = request.GET.get("q", "")
    if it:
        nbr = est_n1_pur(it)
        return JsonResponse({"valide": nbr})
    return JsonResponse({"valide": False})


def deconnecter(request):
    it = request.session.get("it")
    util = None
    if it:
        util = utilisateur.objects.filter(it_id=it).first()

    request.session.flush()

    LoginLog.objects.create(
        utilisateur=util,
        action="LOGOUT",
        ip_address=request.META.get("REMOTE_ADDR")
    )
    return redirect("login")

def _reel_effectif_batch(ru_ids):

    ru_ids = list(ru_ids)
    ru_ids_set = set(ru_ids)
    if not ru_ids:
        return {}

    membres_par_ru = defaultdict(set)
    for c_it, c_ru_it in (
        Collaborateur.objects.filter(ru_it_id__in=ru_ids)
        .exclude(it=F('ru_it_id'))
        .values_list('it', 'ru_it_id')
    ):
        membres_par_ru[c_ru_it].add(c_it)

    dernieres_dates = dict(
        declaration_effectif.objects.filter(Ru_id__in=ru_ids)
        .values('Ru_id').annotate(d=Max('date')).values_list('Ru_id', 'd')
    )

    exclus_par_ru = defaultdict(set)
    for ru_id, collab_id in (
        declaration_effectif.objects.filter(Ru_id__in=ru_ids, nature__in=["C", "D"])
        .values_list('Ru_id', 'collaborateur_it_id')
    ):
        exclus_par_ru[ru_id].add(collab_id)

    ajouts_valides_par_ru = defaultdict(set)
    for ru_id, collab_id, dt in (
        declaration_effectif.objects.filter(Ru_id__in=ru_ids, nature__in=["A", "V"])
        .values_list('Ru_id', 'collaborateur_it_id', 'date')
    ):
        if dernieres_dates.get(ru_id) == dt:
            ajouts_valides_par_ru[ru_id].add(collab_id)

    entrants_candidats = set(
        declaration_effectif.objects.filter(nature="C", nv_Ru_id__in=ru_ids)
        .values_list('collaborateur_it_id', flat=True)
    )

    entrants_valides_par_ru = defaultdict(set)
    if entrants_candidats:
        toutes_decls_candidats = (
            declaration_effectif.objects
            .filter(collaborateur_it_id__in=entrants_candidats)
            .order_by("collaborateur_it_id", "-date", "-id")
            .values_list("collaborateur_it_id", "nature", "nv_Ru_id")
        )
        derniere_par_collab = {}
        for cid, nat, nv_ru_id in toutes_decls_candidats:
            derniere_par_collab.setdefault(cid, (nat, nv_ru_id))

        for cid, (nat, nv_ru_id) in derniere_par_collab.items():
            if nat == "C" and nv_ru_id in ru_ids_set:
                entrants_valides_par_ru[nv_ru_id].add(cid)

    resultat = {}
    for ru_id in ru_ids:
        if ru_id in dernieres_dates:
            base = membres_par_ru.get(ru_id, set()) - exclus_par_ru.get(ru_id, set())
            operateurs = base | ajouts_valides_par_ru.get(ru_id, set())
        else:
            operateurs = set(membres_par_ru.get(ru_id, set()))
        operateurs |= entrants_valides_par_ru.get(ru_id, set())
        operateurs.discard(ru_id)
        resultat[ru_id] = operateurs
    return resultat


@role_required("N+2")
def dashboard_N2(request):
    it = request.session.get("it")
    liste_ru = Ru_Rg(it)
    ru_its = set(liste_ru.values_list("it", flat=True))
    managers_its = get_managers_its()

    operateur = Collaborateur.objects.filter(ru_it=it).exclude(it__in=ru_its)
    nbr_RU = liste_ru.count()
    nbr_op = operateur.count()
    nbr_cadre = (
        Collaborateur.objects.filter(ru_it=it, lot__in=["C", "E"])
        .exclude(it__in=ru_its)
        .count()
    )

    tous_les_it_systeme = get_tous_les_it_sous(it)
    tous_les_it_reel = get_tous_les_it_sous_reel(it)
    total = Collaborateur.objects.filter(it__in=tous_les_it_reel)
    collab = total.exclude(it__in=ru_its)
    nbr_collab = collab.count()

    maint = timezone.localdate()
    today = timezone.now().date()
    dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
    labels_list = [d.strftime("%d %b") for d in dates]

    reel_par_date = get_tous_les_it_sous_reel_evolution(it, dates)
    data_list = [
        len(reel_par_date.get(d, set()))
        for d in dates
    ]

    liste_declares = set(
        declaration_effectif.objects.filter(
            date=maint, Ru_id__in=ru_its
        ).values_list("Ru_id", flat=True)
    )

    liste_ru_avec_operateurs = set(
        Collaborateur.objects.filter(ru_it_id__in=ru_its)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    non_valides = len(liste_ru_avec_operateurs - liste_declares)

    maquette_map = get_maquettes_n1_map(ru_its)
    maquette_n2_obj = MaquetteN1.objects.filter(n1_id=it, actif=True).first()
    maquette_total = maquette_n2_obj.total if maquette_n2_obj else 0

    NB_MOUVEMENTS_PAR_RU = 5
    toutes_declarations = (
        declaration_effectif.objects.filter(
            Ru_id__in=ru_its, nature__in=["D", "C", "A"]
        )
        .select_related("collaborateur_it", "nv_Ru")
        .order_by("Ru_id", "-date", "-id")
    )

    mouvements_par_ru = {}
    for d in toutes_declarations:
        ru_id = d.Ru_id
        if ru_id not in mouvements_par_ru:
            mouvements_par_ru[ru_id] = []
        if len(mouvements_par_ru[ru_id]) < NB_MOUVEMENTS_PAR_RU:
            mouvements_par_ru[ru_id].append(
                {
                    "collaborateur": (
                        d.collaborateur_it.nom_complete
                        if d.collaborateur_it
                        else None
                    ),
                    "collaborateur_it": (
                        d.collaborateur_it.it if d.collaborateur_it else None
                    ),
                    "nature": d.nature,
                    "nature_display": d.get_nature_display(),
                    "nouveau_responsable": (
                        d.nv_Ru.nom_complete if d.nv_Ru else None
                    ),
                    "date": d.date.strftime("%d/%m/%Y"),
                }
            )

    # Systeme/reel calculés EN BLOC pour tous les RU d'un coup.
    systeme_counts = (
        Collaborateur.objects.filter(ru_it_id__in=ru_its)
        .exclude(it__in=managers_its)
        .exclude(it=F('ru_it_id'))
        .values('ru_it_id')
        .annotate(total=Count('it', distinct=True))
    )
    systeme_map = {r['ru_it_id']: r['total'] for r in systeme_counts}
    reel_map = {ru_id: len(collabs) for ru_id, collabs in _reel_effectif_batch(ru_its).items()}

    liste_ru_stats = []
    effectif_syste = 0
    effectif_reel = 0

    for a in liste_ru:
        systeme = systeme_map.get(a.it, 0)
        effectif_syste += systeme
        reel = reel_map.get(a.it, 0)
        effectif_reel += reel
        maquette_obj = maquette_map.get(a.it)
        maquette = maquette_obj.total if maquette_obj else 0
        mr = reel - maquette
        ms = systeme - maquette

        liste_ru_stats.append(
            {
                "matricule": a.matricule,
                "it": a.it,
                "nom_complete": a.nom_complete,
                "lot": a.lot,
                "Equipe": a.eq,
                "dpt": a.departement_id,
                "reel": reel,
                "systeme": systeme,
                "maquette": maquette,
                "MS": ms,
                "MR": mr,
                "MS_abs": abs(ms),
                "MR_abs": abs(mr),
                "derniers_mouvements": mouvements_par_ru.get(a.it, []),
            }
        )

    effectif_syste_total = len(tous_les_it_systeme)
    effectif_reel_total = len(tous_les_it_reel)

    MR = effectif_reel_total - maquette_total
    MS = effectif_syste_total - maquette_total

    def cle_lot(lot):
        if lot in ("A", "O"):
            return "A/O"
        return lot or "Non défini"

    tous_collabs_qs = Collaborateur.objects.filter(it__in=tous_les_it_systeme)

    lots_distincts = sorted(
        set(
            tous_collabs_qs.exclude(lot__isnull=True)
            .exclude(lot="")
            .values_list("lot", flat=True)
        )
    )
    lots_distincts = [lot for lot in lots_distincts if lot not in ("A", "O")]
    if "A/O" not in lots_distincts:
        lots_distincts.append("A/O")
    lots_distincts.sort()

    lot_stats = {
        lot: {"systeme": 0, "reel": 0, "maquette": 0} for lot in lots_distincts
    }

    # Comptage par lot fait en base (GROUP BY).
    for lot_val, total in (
        tous_collabs_qs.values('lot').annotate(total=Count('it')).values_list('lot', 'total')
    ):
        cle = cle_lot(lot_val)
        if cle in lot_stats:
            lot_stats[cle]["systeme"] += total

    for lot_val, total in (
        Collaborateur.objects.filter(it__in=tous_les_it_reel)
        .values('lot').annotate(total=Count('it')).values_list('lot', 'total')
    ):
        cle = cle_lot(lot_val)
        if cle in lot_stats:
            lot_stats[cle]["reel"] += total

    maquette_map_pour_lot = {it: maquette_n2_obj} if maquette_n2_obj else {}
    repartir_maquette_par_lot(maquette_map_pour_lot, lot_stats)

    lot_labels = list(lot_stats.keys())
    lot_reel_data = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_data = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_data = [lot_stats[l]["maquette"] for l in lot_labels]

    context = {
        "chart_labels": json.dumps(labels_list),
        "chart_data": json.dumps(data_list),
        "lot_labels": json.dumps(lot_labels),
        "lot_reel_data": json.dumps(lot_reel_data),
        "lot_systeme_data": json.dumps(lot_systeme_data),
        "lot_maquette_data": json.dumps(lot_maquette_data),
        "effectif_reel": effectif_reel_total,
        "effectif_syste": effectif_syste_total,
        "maquette_total": maquette_total,
        "non_valides": non_valides,
        "liste_ru_stats": liste_ru_stats,
        "MR": MR,
        "MS": MS,
        "MR_abs": abs(MR),
        "MS_abs": abs(MS),
        "maint": maint,
        "nbr_RU": nbr_RU,
        "nbr_op": nbr_op,
        "nbr_cadre": nbr_cadre,
        "op": nbr_op - nbr_cadre,
    }

    return render(
        request, "declaration_effectif/N2/dashboard_N2.html", context
    )

def alerts(request):
    it = request.session.get("it")
    alertes = Alert.objects.filter(recepteur=it, lu=False)
    alertes.update(lu=True)
    return JsonResponse({"statu": "true"})

def changer_role(request, nouveau_role):
    roles_dispo = request.session.get('roles_disponibles', [])
    it=request.session.get("it")
    user=utilisateur.objects.get(it=it)
    if nouveau_role in roles_dispo:
        request.session['role'] = nouveau_role
        user.role=nouveau_role
        user.save()
        messages.success(request, f"Rôle actif changé vers {nouveau_role}")
        return redirect('login')
    else:
        messages.error(request, "Vous n'avez pas ce rôle.")
    return redirect(request.META.get('HTTP_REFERER', 'ru'))


def notifications(request):
    it = request.session.get("it")
    alert = Alert.objects.filter(recepteur=it)
    nv = alert.filter(lu=False).count()
    return {
        "notifications": alert.order_by("-date")[:5],
        "nb_notifications": nv,
    }
@role_required('SUPER')
def SUPER_dashboard(request):
        it=request.session.get("it")
        util = utilisateur.objects.all().exclude(it_id=it)
        total=util.count()
        departements=Departement.objects.all().distinct()
        return render(request,"declaration_effectif/Super/dashboard.html",{"utilisateurs":util,"total":total,"departements":departements})

def generer_mot_de_passe_temporaire(longueur=12):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(alphabet) for _ in range(longueur))

@role_required('SUPER')
def ajouter_user(request):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    try:
        data = json.loads(request.body)
        it_val = data.get("it")
        role_form = data.get("role")

        if not it_val:
            return JsonResponse({"error": "Champs manquants."}, status=400)

        try:
            col = Collaborateur.objects.get(it=it_val)
        except Collaborateur.DoesNotExist:
            return JsonResponse({"error": "Ce collaborateur n'existe pas."}, status=404)

        if utilisateur.objects.filter(it=col).exists():
            return JsonResponse({"error": "Cet utilisateur est déjà enregistré."}, status=400)

        role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it_val)

        role_final = role_form or role_calcule

        if not role_final:
            return JsonResponse({"error": "Impossible de déterminer un rôle pour cet utilisateur."}, status=400)

        mdp_temporaire = generer_mot_de_passe_temporaire()

        util, created = utilisateur.objects.update_or_create(
            it=col,
            defaults={
                "role": role_final,
                "password": make_password(mdp_temporaire),
                "N1": n1,
                "N2": n2,
                "N3": n3,
                "N4": n4,
                "ADMIN": 1 if role_final == "ADMIN" else 0,
                "HRBP": 1 if role_final == "HRBP" else 0,
                "SUPER": 1 if role_final == "SUPER" else 0,"DRH": 1 if role_final == "DRH" else 0,
                "PILOT": 1 if role_final == "PILOT" else 0
            }
        )

        return JsonResponse({
            "message": "Utilisateur enregistré avec succès !",
            "mdp_temporaire": mdp_temporaire,
            "created": created
        }, status=200)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
@role_required('SUPER')
def supprimer_user(request, id):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    it_session = request.session.get("it")
    try:
        util = utilisateur.objects.get(pk=id)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    if util.it_id == it_session:
        return JsonResponse({"error": "Vous ne pouvez pas supprimer votre propre compte."}, status=400)

    util.delete()
    return JsonResponse({"message": "Utilisateur supprimé avec succès."}, status=200)
@role_required('SUPER')
def modifier_user(request, id):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée."}, status=405)

    try:
        util = utilisateur.objects.get(pk=id)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Requête invalide."}, status=400)

    role_form = (data.get("role") or "").strip()

    role_calcule, n1, n2, n3, n4 = determiner_hierarchie(util.it_id)
    role_final = role_form or role_calcule

    if not role_final:
        return JsonResponse({"error": "Impossible de déterminer un rôle pour cet utilisateur."}, status=400)

    util.role = role_final
    util.ADMIN = 1 if role_final == "ADMIN" else 0
    util.HRBP = 1 if role_final == "HRBP" else 0
    util.SUPER = 1 if role_final == "SUPER" else 0
    util.PILOT = 1 if role_final == "PILOT" else 0
    util.DRH = 1 if role_final == "DRH" else 0
    util.N1 = n1
    util.N2 = n2
    util.N3 = n3
    util.N4 = n4
    util.save()

    return JsonResponse({
        "message": "Utilisateur mis à jour avec succès.",
        "role": role_final
    }, status=200)


def calculer_evolution_effectif_reel(departements, jours=30):
    collaborateurs = Collaborateur.objects.filter(departement_id__in=departements)
    liste_collab_ids = list(collaborateurs.values_list("it", flat=True))
    total_collab = len(liste_collab_ids)

    today = timezone.now().date()
    date_debut = today - timedelta(days=jours)

    declarations = declaration_effectif.objects.filter(
        collaborateur_it__in=liste_collab_ids,
        date__lte=today
    ).order_by("collaborateur_it_id", "date").values(
        "collaborateur_it_id", "date", "nature"
    )

    declarations_par_collab = defaultdict(list)
    for d in declarations:
        declarations_par_collab[d["collaborateur_it_id"]].append(
            (d["date"], d["nature"])
        )

    dates_par_collab = {
        collab_id: [d for d, _ in decls]
        for collab_id, decls in declarations_par_collab.items()
    }

    labels = []
    valeurs = []
    date_courante = date_debut
    while date_courante <= today:
        nb_depart = 0
        for collab_id, decls in declarations_par_collab.items():
            dates_list = dates_par_collab[collab_id]
            pos = bisect.bisect_right(dates_list, date_courante) - 1
            if pos >= 0 and decls[pos][1] == "D":
                nb_depart += 1

        labels.append(date_courante.strftime("%d/%m"))
        valeurs.append(total_collab - nb_depart)
        date_courante += timedelta(days=1)

    return {"labels": labels, "valeurs": valeurs}

@role_required(["HRBP", "DRH", "ADMIN"])
def dashboard_rh(request):
    it = request.session.get("it")
    role = request.session.get("role")

    if role == "HRBP":
        y_min = 3480
        y_max = 3510
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "DRH":
        y_min = 6770
        y_max = 6800
        departements_qs = Departement.objects.filter(DRH_id=it)
    elif role == "ADMIN":
        y_min = 3500
        y_max = 3530
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    else:
        y_min = 0
        y_max = 0
        departements_qs = Departement.objects.none()

    departements = list(departements_qs.values_list("abreviation", flat=True))

    colSyst = Collaborateur.objects.filter(departement_id__in=departements).count()

    declarations_activite = (
        declaration_effectif.objects
        .filter(collaborateur_it__departement_id__in=departements, nature__in=["D", "A"])
        .order_by("collaborateur_it_id", "-date", "-id")
    )

    dernier_etat_activite = {}
    for decl in declarations_activite:
        if decl.collaborateur_it_id not in dernier_etat_activite:
            dernier_etat_activite[decl.collaborateur_it_id] = decl.nature

    liste_D = {c for c, nature in dernier_etat_activite.items() if nature == "D"}
    
    colReel = Collaborateur.objects.filter(
        departement_id__in=departements
    ).exclude(it__in=liste_D).count()
    _, l1, l2, l3, l4 = calculer_niveaux_hierarchie()
    ids_managers_perimetre = set(
        Collaborateur.objects.filter(
            it__in=(l1 | l2 | l3 | l4), departement_id__in=departements
        ).values_list("it", flat=True)
    )
    maquette, maquette_map_racines, racines, somme_ap, somme_ce = calculer_maquette_totale_perimetre(
        ids_managers_perimetre
    )

    syst_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )

    reel_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements)
        .exclude(it__in=liste_D)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )

    depart_par_dept = dict(
        Collaborateur.objects.filter(departement_id__in=departements, it__in=liste_D)
        .values("departement_id")
        .annotate(total=Count("it"))
        .values_list("departement_id", "total")
    )
    racines_par_dept = defaultdict(set)
    if racines:
        for it_m, dept_m in Collaborateur.objects.filter(
            it__in=racines
        ).values_list("it", "departement_id"):
            racines_par_dept[dept_m].add(it_m)

    depart_TOT = 0
    detail_par_dept = []
    for dept in departements_qs:
        abrev = dept.abreviation
        syst_dept = syst_par_dept.get(abrev, 0)
        reel_dept = reel_par_dept.get(abrev, 0)
        nb_depart_dept = depart_par_dept.get(abrev, 0)
        depart_TOT += nb_depart_dept

        racines_dept = racines_par_dept.get(abrev, set())
        maquette_dept = sum(
            maquette_map_racines[r].total
            for r in racines_dept
            if r in maquette_map_racines
        )

        detail_par_dept.append({
            "abreviation": abrev,
            "nom": getattr(dept, "nom", abrev),
            "syst": syst_dept,
            "reel": reel_dept,
            "nb_depart": nb_depart_dept,
            "maquette": maquette_dept,
            "ms": syst_dept - maquette_dept,
            "mr": reel_dept - maquette_dept,
        })

    today = timezone.now().date()
    ev = _calculer_evolution_reel(departements, today)

    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)
    responsables_directs = set(
        collaborateurs_base
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    operateur_n1 = Collaborateur.objects.filter(
        departement_id__in=departements, lot__in=["A", "O", "P"]
    ).exclude(it__in=responsables_directs)

    ru_ids = set(
        operateur_n1
        .exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    ru_declares = set(
        declaration_effectif.objects
        .filter(date=today, Ru_id__in=ru_ids)
        .values_list("Ru_id", flat=True)
    )
    ru_non_declares = ru_ids - ru_declares

    ru_total = len(ru_ids)
    non_valides = len(ru_non_declares)

    date_depart = request.GET.get("date_depart")
    date_changement = request.GET.get("date_changement")

    liste_departs_qs = declaration_effectif.objects.filter(
        collaborateur_it__departement_id__in=departements,
        nature="D",
        collaborateur_it_id__in=liste_D,
    )
    if date_depart:
        liste_departs_qs = liste_departs_qs.filter(date=date_depart)

    nbr_departs_total = liste_departs_qs.count()

    liste_departs = liste_departs_qs.select_related(
        "collaborateur_it", "collaborateur_it__departement", "Ru"
    ).order_by("-date")[:10]

    liste_changements_qs = declaration_effectif.objects.filter(
        collaborateur_it__departement_id__in=departements,
        nature="C"
    )
    if date_changement:
        liste_changements_qs = liste_changements_qs.filter(date=date_changement)

    nbr_changements_total = liste_changements_qs.count()

    liste_changements = liste_changements_qs.select_related(
        "collaborateur_it", "collaborateur_it__departement", "Ru", "nv_Ru"
    ).order_by("-date")[:10]

    context = {
        "departements": departements,
        "colSyst": colSyst,
        "colReel": colReel,
        "today": today,
        "maquette": maquette,
        "MS": colSyst - maquette,
        "MR": colReel - maquette,
        "labels": ev["mois_labels_annee"],
        "valeurs": ev["evolution_annee"],
        "detail_par_dept": detail_par_dept,
        "depart_TOT": depart_TOT,
        "liste_departs": liste_departs,
        "liste_changements": liste_changements,
        "nbr_departs_total": nbr_departs_total,
        "nbr_changements_total": nbr_changements_total,
        "non_valides": non_valides,
        "ru_total": ru_total,
        "y_min": y_min,
        "y_max": y_max,
        "date_depart": date_depart or "",
        "date_changement": date_changement or "",
        "somme_ap": somme_ap, "somme_ce": somme_ce,
    }

    return render(request, "declaration_effectif/HRBP/dashboard.html", context)

def derniers_mouvements_respo(it_respo, limite=10):
    if not it_respo:
        return []

    declarations = (
        declaration_effectif.objects
        .filter(Ru__it=it_respo,nature__in=["A","D","C"])
        .select_related('collaborateur_it', 'Ru', 'nv_Ru')
        .order_by('-date', '-id')[:limite]
    )

    return [
        {
            "id": d.pk,
            "collaborateur": d.collaborateur_it.nom_complete if d.collaborateur_it else None,
            "collaborateur_it": d.collaborateur_it.it if d.collaborateur_it else None,
            "nature": d.nature,
            "nature_display": d.get_nature_display(),
            "nouveau_responsable": d.nv_Ru.nom_complete if d.nv_Ru else None,
            "nouveau_responsable_it": d.nv_Ru.it if d.nv_Ru else None,
            "date": d.date.strftime("%d/%m/%Y"),
        }
        for d in declarations
    ]


def changer_mot_de_passe(request):
    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "Session expirée, veuillez vous reconnecter."}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Requête invalide."}, status=400)

    ancien_password = data.get("ancien_password", "")
    nouveau_password = data.get("nouveau_password", "")

    if not ancien_password or not nouveau_password:
        return JsonResponse({"error": "Tous les champs sont requis."}, status=400)

    if len(nouveau_password) < 8:
        return JsonResponse({"error": "Le nouveau mot de passe doit contenir au moins 8 caractères."}, status=400)

    try:
        user = utilisateur.objects.get(it_id=it)
    except utilisateur.DoesNotExist:
        return JsonResponse({"error": "Utilisateur introuvable."}, status=404)

    if not user.check_password(ancien_password):
        return JsonResponse({"error": "Mot de passe actuel incorrect."}, status=400)

    user.set_password(nouveau_password)
    user.save()

    return JsonResponse({"success": True})

def _get_departs_et_changements(departement):
    collab_dpt = Collaborateur.objects.filter(departement_id=departement.abreviation)
    matricules_dpt = set(collab_dpt.values_list('it', flat=True))
    collab_map = {c.it: c for c in collab_dpt}

    departs_qs = declaration_effectif.objects.filter(
        nature='D', collaborateur_it__in=matricules_dpt
    ).order_by('-date')

    liste_departs = []
    for d in departs_qs:
        collab = collab_map.get(d.collaborateur_it_id)
        liste_departs.append({
            "it": d.collaborateur_it_id,
            "nom": collab.nom_complete if collab else "-",
            "dpt":collab.departement_id if collab else "-",
            "lot": collab.lot if collab else "-",
            "date_declaration": d.date,
            "ru": d.Ru_id,
        })

    changements_qs = declaration_effectif.objects.filter(
        nature='C', collaborateur_it__in=matricules_dpt
    ).order_by('-date')

    liste_changements_non_faits = []
    for c in changements_qs:
        collab = collab_map.get(c.collaborateur_it_id)
        if not collab:
            continue

        diffs = {}

        nouveau_ru = c.nv_Ru_id
        if nouveau_ru is not None and str(nouveau_ru) != str(collab.ru_it_id):
            diffs['ru_it'] = {"ancien": collab.ru_it_id, "nouveau": nouveau_ru}

        if diffs:
            liste_changements_non_faits.append({
                "it": c.collaborateur_it_id,
                "nom": collab.nom_complete,
                "dpt":c.collaborateur_it.departement_id if collab else "-",
                "date_declaration": c.date,
                "ru": c.Ru_id,
                "diffs": diffs,
                "dpt_init":c.Ru.departement_id,"dpt_acc":c.nv_Ru.departement_id,
            })

    return {
        "liste_departs": liste_departs,
        "nbr_departs": len(liste_departs),
        "liste_changements_non_faits": liste_changements_non_faits,
        "nbr_changements_non_faits": len(liste_changements_non_faits),
    }


def pilot_departs_changements(request):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)
    context = {
        "departement": departement,
        **_get_departs_et_changements(departement),
    }
    return render(request, "declaration_effectif/PILOT/departs_changements.html", context)


def _calculer_evolution_reel(departement_ids, today):
    

    premier_mois_annee = date(today.year, 1, 1)
    dates_ref_annee, mois_labels_annee = [], []
    curseur = premier_mois_annee
    while curseur.year == today.year and curseur <= today:
        dernier_jour_mois = calendar.monthrange(curseur.year, curseur.month)[1]
        dates_ref_annee.append(min(date(curseur.year, curseur.month, dernier_jour_mois), today))
        mois_labels_annee.append(curseur.strftime("%m/%Y"))
        curseur = date(curseur.year + 1, 1, 1) if curseur.month == 12 else date(curseur.year, curseur.month + 1, 1)

    premier_jour_mois = date(today.year, today.month, 1)
    dates_jour_mois_courant = []
    curseur_jour = premier_jour_mois
    while curseur_jour <= today:
        dates_jour_mois_courant.append(curseur_jour)
        curseur_jour += timedelta(days=1)

    jours_labels_mois_courant = [d.strftime("%d/%m") for d in dates_jour_mois_courant]

    ids_par_date = get_effectif_reel_ids_multi(
        departement_ids, dates_ref_annee + dates_jour_mois_courant
    )

    return {
        "dates_ref_annee": dates_ref_annee,
        "mois_labels_annee": mois_labels_annee,
        "evolution_annee": [len(ids_par_date[d]) for d in dates_ref_annee],
        "jours_labels_mois_courant": jours_labels_mois_courant,
        "evolution_mois_courant": [len(ids_par_date[d]) for d in dates_jour_mois_courant],
    }


def _evolution_maquette_par_lot(racines, dates_ref_annee, lots_suivis):
    evolution = {lot: [] for lot in lots_suivis}

    for d in dates_ref_annee:

        lot_stats_mois = {
            lot: {
                "reel": 0,
                "systeme": 0,
                "maquette": 0
            }
            for lot in lots_suivis
        }

        # IMPORTANT :
        # on utilise les racines et non ru_ids
        maquettes_a_d = get_maquettes_a_date(racines, d)

        repartir_maquette_par_lot(
            maquettes_a_d,
            lot_stats_mois
        )

        for lot in lots_suivis:
            evolution[lot].append(
                lot_stats_mois[lot]["maquette"]
            )

    return evolution

def _stats_et_evolutions_par_ru(responsables, operateur, ids_total_r, maquette_map,
                                dates_ref_ru, mois_labels_ru, dates_jour, jours_labels):
    liste_ru_stats = []
    ru_evolution = {}
    ru_evolution_jour = {}

    ru_ids = [ru.it for ru in responsables]

    maquettes_par_date = {
        d: get_maquettes_a_date(ru_ids, d) for d in dates_ref_ru
    }
    toutes_dates = sorted(set(dates_ref_ru) | set(dates_jour))
    reel_evolution = reelEff_evolution_multi(ru_ids, toutes_dates)

    reel_actuel_par_ru = {
        ru_id: len(collabs) for ru_id, collabs in _reel_effectif_batch(ru_ids).items()
    }

    for ru in responsables:
        equipe = operateur.filter(ru_it_id=ru.it)
        systeme = equipe.count()
        reel = reel_actuel_par_ru.get(ru.it, 0)

        maquette_obj = maquette_map.get(ru.it)
        maquette_ru = maquette_obj.total if maquette_obj else 0
        liste_ru_stats.append({
            "n1": ru, "equipe": ru.eq,
            "systeme": systeme, "ms": systeme - maquette_ru,
            "reel": reel, "mr": reel - maquette_ru,
            "maquette": maquette_ru,
        })

        ev_ru = reel_evolution.get(ru.it, {})

        reel_series = [len(ev_ru.get(d, set())) for d in dates_ref_ru]
        maquette_series = []
        for d in dates_ref_ru:
            snap = maquettes_par_date[d].get(ru.it)
            maquette_series.append((snap.A + snap.T + snap.P + snap.C) if snap else 0)

        ru_evolution[ru.it] = {
            "nom": ru.nom_complete, "labels": mois_labels_ru,
            "reel": reel_series, "systeme": systeme, "maquette": maquette_series,
        }

        reel_series_jour = [len(ev_ru.get(d, set())) for d in dates_jour]
        ru_evolution_jour[ru.it] = {
            "nom": ru.nom_complete, "labels": jours_labels,
            "reel": reel_series_jour, "systeme": systeme, "maquette": maquette_ru,
        }

    return liste_ru_stats, ru_evolution, ru_evolution_jour

def pilot_dashboard(request):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)
    departement_ids = [departement.abreviation]
    collab = Collaborateur.objects.filter(departement_id=departement.abreviation)
    total_syst = collab.count()

    today = timezone.now().date()
    ids_total_r = get_effectif_reel_ids(departement_ids, today)
    total_r = len(ids_total_r)

    operateur = Collaborateur.objects.filter(
        departement_id=departement.abreviation, lot__in=["A", "O", "P"]
    ).exclude(ru_it_id__isnull=True).exclude(ru_it_id=F('it'))
    _, l1, l2, l3, l4 = calculer_niveaux_hierarchie()
    ru_ids = l1 & set(operateur.filter(ru_it__isnull=False, ru_it__departement=departement).values_list("ru_it_id", flat=True))
    ru_ids.discard(None)
    ids_managers_departement = set(
        Collaborateur.objects.filter(
            it__in=(l1 | l2 | l3 | l4), departement=departement
        ).values_list("it", flat=True)
    )
    maquette_totale, maquette_map_racines, racines ,somme_ap,somme_ce= calculer_maquette_totale_perimetre(
        ids_managers_departement, departement=departement
    )
    MR = total_r - maquette_totale
    MS = total_syst - maquette_totale

    ru_declarer = set(
        declaration_effectif.objects
        .filter(date=today, Ru_id__in=ru_ids)
        .values_list("Ru_id", flat=True)
    )
    ru_non_declarer = ru_ids - ru_declarer

    responsables = Collaborateur.objects.filter(it__in=ru_ids).select_related('departement', 'ru_it')
    n2 = l2 & set(
        responsables.filter(ru_it__isnull=False, ru_it__departement=departement)
        .values_list("ru_it_id", flat=True)
    )
    nbr_n2 = len(n2)
    n3 = l3 & set(
        Collaborateur.objects.filter(it__in=n2, ru_it__isnull=False, ru_it__departement=departement)
        .values_list("ru_it_id", flat=True)
    )
    nbr_n3 = len(n3)

    maquette_map = get_maquettes_n1_map(ru_ids, departement=departement)
    lots_suivis = ("A", "P", "E", "C")

    ev = _calculer_evolution_reel(departement_ids, today)
    annee_actuelle = str(today.year)

    evolution_maquette_par_lot = _evolution_maquette_par_lot(racines, ev["dates_ref_annee"], lots_suivis)

    annees_ru, mois_cles_ru, mois_labels_ru, dates_ref_ru = _dates_fin_de_mois(nb_annees=1)
    dates_ref_ru = [min(d, today) for d in dates_ref_ru]
    dates_jour = [today - timedelta(days=i) for i in range(29, -1, -1)]
    jours_labels = [d.strftime("%d/%m") for d in dates_jour]

    liste_ru_stats, ru_evolution, ru_evolution_jour = _stats_et_evolutions_par_ru(
        responsables, operateur, ids_total_r, maquette_map,
        dates_ref_ru, mois_labels_ru, dates_jour, jours_labels,
    )

    def cle_lot(lot):
        if lot in ("A", "O"):
            return "A/O"
        return lot or "Non défini"

    lots_distincts = sorted({
        cle_lot(l) for l in collab.exclude(lot__isnull=True).exclude(lot="")
        .values_list("lot", flat=True)
    })
    if "A/O" not in lots_distincts:
        lots_distincts.append("A/O")
    lots_distincts.sort()

    lot_stats = {lot: {"systeme": 0, "reel": 0, "maquette": 0} for lot in lots_distincts}

    for lot_val, total in (
        collab.values('lot').annotate(total=Count('it')).values_list('lot', 'total')
    ):
        cle = cle_lot(lot_val)
        if cle in lot_stats:
            lot_stats[cle]["systeme"] += total

    for lot_val, total in (
        Collaborateur.objects.filter(it__in=ids_total_r)
        .values('lot').annotate(total=Count('it')).values_list('lot', 'total')
    ):
        cle = cle_lot(lot_val)
        if cle in lot_stats:
            lot_stats[cle]["reel"] += total


    repartir_maquette_par_lot(maquette_map_racines, lot_stats)

    lot_labels = list(lot_stats.keys())
    lot_reel_totaux = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_totaux = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_totaux = [lot_stats[l]["maquette"] for l in lot_labels]

    dc_data = _get_departs_et_changements(departement)

    context = {
        "maint": timezone.now().date(),
        "total_syst": total_syst,
        "total_r": total_r,
        "maquette_totale": maquette_totale,
        "MR": MR,
        "MS": MS,
        "ru_total": len(ru_ids),
        "ru_declarer_count": len(ru_declarer),
        "non_valides": len(ru_non_declarer),
        "ru_non_declarer": ru_non_declarer,
        "liste_ru_stats": liste_ru_stats,

        "evolution_reel_labels_annee_json": json.dumps(ev["mois_labels_annee"]),
        "evolution_reel_data_annee_json": json.dumps(ev["evolution_annee"]),
        "annee_actuelle": annee_actuelle,

        "evolution_reel_labels_mois_courant_json": json.dumps(ev["jours_labels_mois_courant"]),
        "evolution_reel_data_mois_courant_json": json.dumps(ev["evolution_mois_courant"]),

        "evolution_maquette_lot_labels_json": json.dumps(ev["mois_labels_annee"]),
        "evolution_maquette_par_lot_json": json.dumps(evolution_maquette_par_lot),

        "ru_evolution_json": json.dumps(ru_evolution),
        "ru_evolution_jour_json": json.dumps(ru_evolution_jour),

        "lot_labels_json": json.dumps(lot_labels),
        "lot_reel_json": json.dumps(lot_reel_totaux),
        "lot_systeme_json": json.dumps(lot_systeme_totaux),
        "lot_maquette_json": json.dumps(lot_maquette_totaux),

        "nbr_n2": nbr_n2, "nbr_n3": nbr_n3,"n2":Collaborateur.objects.filter(it__in=n2),"n3":Collaborateur.objects.filter(it__in=n3),

        "liste_departs": dc_data["liste_departs"],
        "nbr_departs": dc_data["nbr_departs"],
        "liste_changements_non_faits": dc_data["liste_changements_non_faits"],
        "nbr_changements_non_faits": dc_data["nbr_changements_non_faits"],"somme_ap":somme_ap,"somme_ce":somme_ce,
    }
    return render(request, "declaration_effectif/PILOT/dashboard.html", context)

def declaration(request):

    ru_non_declarer = ru_nn_valider_par_departement(request)

    qs = Collaborateur.objects.filter(it__in=ru_non_declarer).order_by('it')

    page_number = request.GET.get('page', 1)
    try:
        per_page = int(request.GET.get('per_page', 10))
    except (TypeError, ValueError):
        per_page = 7

    paginator = Paginator(qs, per_page)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    today = timezone.now().date()
    context = {
        "resultat": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "non_valides": len(ru_non_declarer),
        "date": today.isoformat(),
    }
    return render(request, "declaration_effectif/PILOT/declaration.html", context)

def ru_nn_valider_par_departement(request, date=None):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)
    if date is None:
        date = timezone.now().date()

    operateur = Collaborateur.objects.filter(
        departement_id=departement.abreviation, lot__in=["A", "O", "P"]
    ).exclude(ru_it_id__isnull=True).exclude(ru_it_id=F('it'))
    _, l1, _, _, _ = calculer_niveaux_hierarchie()
    ru = l1 & set(operateur.filter(ru_it__isnull=False , ru_it__departement=departement).values_list("ru_it_id", flat=True))
    ru.discard(None)

    ru_declarer = set(
        declaration_effectif.objects
        .filter(date=date, Ru_id__in=ru)
        .values_list("Ru_id", flat=True)
    )

    ru_non_declarer = ru - ru_declarer
    return ru_non_declarer


def filter_date_DPT(request):
    date_str = request.GET.get('time')
    if not date_str:
        return JsonResponse({"resultats": [], "status": False})

    date_obj = parse_date(date_str)
    if date_obj is None:
        return JsonResponse({"resultats": [], "status": False})

    is_today = (date_obj == timezone.localdate())

    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)

    operateur = Collaborateur.objects.filter(
        departement_id=departement.abreviation, lot__in=["A", "O", "P"]
    ).exclude(ru_it_id__isnull=True).exclude(ru_it_id=F('it'))

    _, l1, _, _, _ = calculer_niveaux_hierarchie()
    ru = l1 & set(
    operateur.filter(ru_it__isnull=False, ru_it__departement=departement)
    .values_list("ru_it_id", flat=True))
    ru.discard(None)

    ru_declarer = set(
        declaration_effectif.objects
        .filter(date=date_obj, Ru_id__in=ru)
        .values_list("Ru_id", flat=True)
    )

    ru_non_declarer = ru - ru_declarer
    status = not is_today

    resultats = list(
        Collaborateur.objects.filter(it__in=ru_non_declarer)
        .values("it", "matricule", "nom_complete", "departement_id", "eq", "lot")
    )
    return JsonResponse({"resultats": resultats, "status": status})