from django.http import JsonResponse
from django.urls import include, path


def raiz(_request):
    return JsonResponse({
        "servicio": "SIPRO FIRE backend (Django)",
        "documentacion": "/api/estado",
        "rutas": [
            "GET  /api/estado",
            "POST /api/auth/login",
            "GET  /api/auth/yo",
            "POST /api/auth/logout",
            "GET  /api/ambiente/meteo?lat&lon",
            "GET  /api/ambiente/climatologia?lat&lon",
            "GET  /api/ambiente/firms",
            "GET  /api/ambiente/terreno/osm",
            "GET  /api/ambiente/terreno/dem?fila&columna&radio",
            "GET  /api/simulacion/parametros-auto?fila&columna&horas",
            "POST /api/simulacion/ejecutar",
            "GET  /api/simulacion/:id",
            "GET  /api/escenarios",
            "GET  /api/escenarios/:id",
            "DELETE /api/escenarios/:id",
            "GET  /api/alertas/riesgo",
            "GET  /api/escenarios/:id/alertas",
            "GET  /api/escenarios/:id/grafica",
            "GET  /api/calibracion",
            "POST /api/calibracion",
            "GET  /api/usuarios",
            "POST/PATCH/DELETE /api/usuarios/:id",
            "GET  /api/permisos",
            "PUT  /api/permisos",
            "GET  /api/historicos",
            "GET/POST /api/bitacora",
            "GET/POST /api/informes",
        ],
    })


def no_encontrada(_request, exception=None):
    return JsonResponse({"ok": False, "error": "Ruta no encontrada"}, status=404)


urlpatterns = [
    path("", raiz),
    path("api/", include("api.urls")),
]

handler404 = no_encontrada
