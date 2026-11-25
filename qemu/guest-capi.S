/* Isolated x86-64 Linux guest client. Raw syscalls, no libc or host ABI.
 * Linux v4.9 include/uapi/linux/capi.h and drivers/isdn/capi/capi.c.
 * All labels/data are in one section; the packer rejects relocations.
 */
.intel_syntax noprefix
.text
.global _start
_start:
    sub rsp, 1024
    mov r15, rsp
    mov ebx, 1
    mov eax, 2                         /* open /dev/capi20, RDWR|NONBLOCK */
    lea rdi, [rip + .Ldevice]
    mov esi, 2050
    xor edx, edx
    syscall
    test rax, rax
    js .Lfail
    mov r12, rax
    mov ebx, 2
    mov dword ptr [r15], 8             /* CAPI_REGISTER: L3, blocks, length */
    mov dword ptr [r15 + 4], 8
    mov dword ptr [r15 + 8], 2048
    mov eax, 16
    mov rdi, r12
    mov esi, 0x400c4301
    mov rdx, r15
    syscall
    test eax, eax
    jle .Lfail
    mov r13d, eax
    mov ebx, 3
    mov dword ptr [r15], 0             /* GET_PROFILE controller zero */
    call .Lprofile
    cmp word ptr [r15], 4
    jne .Lfail
    mov r14d, 1
.Lcontroller:
    mov ebx, 3
    mov dword ptr [r15], r14d
    call .Lprofile
    cmp word ptr [r15 + 2], 2
    jne .Lfail
    lea rsi, [rip + .Lprofile_ok]
    mov edx, offset .Lprofile_len
    call .Lemit_controller

    /* LISTEN_REQ: length/app/cmd/subcmd/msg/controller/three masks/two structs */
    mov word ptr [r15 + 64], 26
    mov word ptr [r15 + 66], r13w
    mov word ptr [r15 + 68], 0x8005
    mov word ptr [r15 + 70], r14w
    mov dword ptr [r15 + 72], r14d
    mov dword ptr [r15 + 76], 0
    mov dword ptr [r15 + 80], 1         /* CIP wildcard */
    mov dword ptr [r15 + 84], 0
    mov word ptr [r15 + 88], 0
    mov ebx, 4
    mov eax, 1
    mov rdi, r12
    lea rsi, [r15 + 64]
    mov edx, 26
    syscall
    cmp rax, 26
    jne .Lfail
    mov ebx, 5
    mov dword ptr [r15 + 256], r12d
    mov dword ptr [r15 + 260], 1        /* pollfd POLLIN, revents=0 */
    mov eax, 7
    lea rdi, [r15 + 256]
    mov esi, 1
    mov edx, 2000
    syscall
    cmp rax, 1
    jne .Lfail
    test word ptr [r15 + 262], 1
    jz .Lfail
    mov ebx, 6
    xor eax, eax
    mov rdi, r12
    lea rsi, [r15 + 64]
    mov edx, 192
    syscall
    cmp rax, 14
    jne .Lfail
    cmp word ptr [r15 + 64], 14
    jne .Lfail
    cmp word ptr [r15 + 66], r13w
    jne .Lfail
    cmp word ptr [r15 + 68], 0x8105
    jne .Lfail
    cmp word ptr [r15 + 70], r14w
    jne .Lfail
    cmp dword ptr [r15 + 72], r14d
    jne .Lfail
    cmp word ptr [r15 + 76], 0          /* LISTEN_CONF Info=success */
    jne .Lfail
    lea rsi, [rip + .Llisten_ok]
    mov edx, offset .Llisten_len
    call .Lemit_controller
    inc r14d
    cmp r14d, 5
    jne .Lcontroller
    mov ebx, 7
    mov eax, 3
    mov rdi, r12
    syscall
    test eax, eax
    jne .Lfail
    lea rsi, [rip + .Lok]
    mov edx, offset .Lok_len
    call .Lemit
    xor edi, edi
    mov eax, 60
    syscall
.Lprofile:
    mov eax, 16
    mov rdi, r12
    mov esi, 0xc0404309
    mov rdx, r15
    syscall
    test eax, eax
    jne .Lfail
    ret
.Lemit_controller:
    mov r8, rdx
    lea rdi, [r15 + 512]
    mov rcx, rdx
    rep movsb
    mov al, r14b
    add al, '0'
    mov byte ptr [rdi], al
    mov byte ptr [rdi + 1], 10
    lea rsi, [r15 + 512]
    lea rdx, [r8 + 2]
.Lemit:
    mov eax, 1
    mov edi, 1
    syscall
    ret
.Lemit_hex:
    mov r9d, eax
    mov r8, rdx
    lea rdi, [r15 + 512]
    mov rcx, rdx
    rep movsb
    mov ecx, 8
    lea rsi, [rip + .Lhex]
.Lhex_loop:
    rol r9d, 4
    mov eax, r9d
    and eax, 15
    mov al, byte ptr [rsi + rax]
    mov byte ptr [rdi], al
    inc rdi
    loop .Lhex_loop
    mov byte ptr [rdi], 10
    lea rsi, [r15 + 512]
    lea rdx, [r8 + 9]
    jmp .Lemit
.Lfail:
    mov dword ptr [r15 + 320], eax
    mov word ptr [r15 + 324], 0
    mov eax, 16
    mov rdi, r12
    mov esi, 0x80024321              /* GET_ERRCODE after failed write */
    lea rdx, [r15 + 324]
    syscall
    movzx eax, word ptr [r15 + 324]
    lea rsi, [rip + .Lerror]
    mov edx, offset .Lerror_len
    call .Lemit_hex
    mov eax, dword ptr [r15 + 320]
    lea rsi, [rip + .Lresult]
    mov edx, offset .Lresult_len
    call .Lemit_hex
    lea rsi, [rip + .Lfailed]
    mov edx, offset .Lfailed_len
    call .Lemit
    mov edi, ebx
    mov eax, 60
    syscall
.Lhex: .ascii "0123456789abcdef"
.Lerror: .ascii "BRI_CAPI_ERROR code="
.set .Lerror_len, . - .Lerror
.Lresult: .ascii "BRI_CAPI_RESULT value="
.set .Lresult_len, . - .Lresult
.Ldevice: .asciz "/dev/capi20"
.Lprofile_ok: .ascii "BRI_CAPI_PROFILE_OK controller="
.set .Lprofile_len, . - .Lprofile_ok
.Llisten_ok: .ascii "BRI_CAPI_LISTEN_OK controller="
.set .Llisten_len, . - .Llisten_ok
.Lok: .ascii "BRI_CAPI_CLIENT_OK\n"
.set .Lok_len, . - .Lok
.Lfailed: .ascii "BRI_CAPI_CLIENT_FAILED\n"
.set .Lfailed_len, . - .Lfailed
.section .note.GNU-stack,"",@progbits
