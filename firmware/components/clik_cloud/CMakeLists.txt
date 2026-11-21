idf_component_register(SRCS "clik_cloud.c" INCLUDE_DIRS "include"
    REQUIRES clik_usb clik_board esp_http_client esp-tls esp_timer json lwip)
