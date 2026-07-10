from django.urls import path

from . import views


urlpatterns = [
    path('', views.home_view, name='home'),
    path('camera/', views.camera_view, name='camera'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('api/state/', views.state_api, name='state_api'),
    path('api/camera/state/', views.camera_state_api, name='camera_state_api'),
    path('api/set_counter/', views.set_counter_api, name='set_counter_api'),
    path('api/capture_timing/', views.capture_timing_api, name='capture_timing_api'),
    path('api/manual_capture/', views.manual_capture_api, name='manual_capture_api'),
    path('api/esp32_trigger/', views.esp32_trigger_api, name='esp32_trigger_api'),
    path('api/esp32/command/', views.esp32_command_api, name='esp32_command_api'),
    path('api/esp32/report/', views.esp32_report_api, name='esp32_report_api'),
    path('api/recapture/', views.recapture_api, name='recapture_api'),
    path('api/capture_started/', views.capture_started_api, name='capture_started_api'),
    path('api/upload_images/', views.upload_images_api, name='upload_images_api'),
    path('api/classify/', views.classify_api, name='classify_api'),
    path('api/discard/', views.discard_api, name='discard_api'),
    path('api/reset_dataset/', views.reset_dataset_api, name='reset_dataset_api'),
    path('api/image/<str:fruit_id>/<str:filename>/', views.dataset_image_api, name='dataset_image_api'),
    path('api/open_dataset_folder/', views.open_dataset_folder_api, name='open_dataset_folder_api'),
    path('api/webrtc/offer', views.webrtc_offer_api, name='webrtc_offer_api'),
    path('api/webrtc/answer', views.webrtc_answer_api, name='webrtc_answer_api'),
    path('api/webrtc/ice', views.webrtc_ice_api, name='webrtc_ice_api'),
    path('api/webrtc/state', views.webrtc_state_api, name='webrtc_state_api'),
]
