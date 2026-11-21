idf_component_register(SRCS "main.c" "provision.c"
    INCLUDE_DIRS "."
    REQUIRES clik_usb clik_protocol clik_board clik_cloud clik_hid
        esp_wifi esp_event esp_netif esp_http_server esp_timer nvs_flash json lwip
    EMBED_TXTFILES "local.html")
