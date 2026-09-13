from django.urls import include, path

urlpatterns = [
    path("", include("dermatology.app_urls")),
]