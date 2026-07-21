from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # 使用 include 將根目錄路由指引至 fruit_app 內部的 urls.py
    path('', include('fruit_app.urls')), 
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
