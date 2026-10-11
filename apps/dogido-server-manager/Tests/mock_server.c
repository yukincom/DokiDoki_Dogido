#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }

int main(int argc, char **argv) {
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    setvbuf(stdout, NULL, _IONBF, 0);
    puts("mock started");
    if (argc > 1 && strcmp(argv[1], "voice-input") == 0) {
        while (running) usleep(10000);
        puts("mock stopped");
        return 0;
    }
    const char *endpoint = NULL;
    for (int i = 2; i + 1 < argc; i++) if (strcmp(argv[i], "--listen") == 0) endpoint = argv[i + 1];
    if (!endpoint) return 2;
    int fd = socket(AF_INET, SOCK_STREAM, 0), reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons((unsigned short)atoi(strrchr(endpoint, ':') + 1));
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) != 0) return 3;
    if (listen(fd, 4) != 0) return 5;
    while (running) {
        fd_set reads; FD_ZERO(&reads); FD_SET(fd, &reads);
        struct timeval timeout = {0, 100000};
        if (select(fd + 1, &reads, NULL, NULL, &timeout) <= 0) continue;
        int client = accept(fd, NULL, NULL);
        if (client < 0) continue;
        char request[1024]; read(client, request, sizeof(request));
        const char *body = "{\"ok\":true,\"runtime\":\"rust\",\"dialogue_ready\":true}";
        dprintf(client, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n%s", strlen(body), body);
        close(client);
    }
    close(fd); puts("mock stopped"); return 0;
}
