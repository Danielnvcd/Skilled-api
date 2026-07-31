"""Crea (o promueve) la primera cuenta con rol `sistemas`.

┌─ POR QUÉ HACE FALTA ────────────────────────────────────────────────────────┐
│ La gestión de cuentas se movió de `admin` (que quedó para RRHH) al rol      │
│ `sistemas`. Justo después de desplegar ese cambio:                          │
│                                                                             │
│   - no existe ninguna cuenta con rol `sistemas`, y                          │
│   - los admin de RRHH ya NO pueden dar de alta usuarios.                    │
│                                                                             │
│ O sea que nadie podría crear la primera cuenta desde la aplicación, salvo   │
│ un `super_admin` si es que hay uno a la mano. Este script rompe ese huevo   │
│ y la gallina. Se ejecuta UNA vez, en el servidor.                           │
└─────────────────────────────────────────────────────────────────────────────┘

Uso (en el VPS, con el venv de la app):

    cd /opt/nominas
    venv/bin/python scripts/crear_cuenta_sistemas.py --usuario ti.soporte

Pide la contraseña de forma interactiva (no se pasa por argumento para que no
quede registrada en el historial del shell ni en la lista de procesos).

Para PROMOVER una cuenta que ya existe en vez de crear una nueva:

    venv/bin/python scripts/crear_cuenta_sistemas.py --usuario juan --promover

El script es idempotente: si la cuenta ya tiene rol `sistemas`, no hace nada.

DESPUÉS DE EJECUTARLO: entra con esa cuenta y activa el 2FA en Mi Perfil. El
panel de sistemas no abre sin segundo factor — es a propósito, porque ese rol
puede crear cuentas y cerrar sesiones ajenas.
"""
import argparse
import getpass
import os
import sys

# Permite ejecutar el script desde la raíz del proyecto sin instalar el paquete.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usuario', required=True, help='nombre de usuario')
    parser.add_argument(
        '--promover', action='store_true',
        help='cambia el rol de una cuenta existente en lugar de crear una nueva',
    )
    args = parser.parse_args()

    from app import create_app
    from app.extensions import db
    from app.models import User
    from app.utils import is_strong_password

    app = create_app()
    with app.app_context():
        existente = User.query.filter_by(username=args.usuario).first()

        if args.promover:
            if not existente:
                print(f"ERROR: no existe el usuario '{args.usuario}'.", file=sys.stderr)
                return 1
            if existente.role == 'sistemas':
                print(f"'{args.usuario}' ya tiene el rol sistemas. Nada que hacer.")
                return 0
            anterior = existente.role
            existente.role = 'sistemas'
            # Subir password_version invalida sus JWT vivos: el cambio de rol
            # debe reflejarse de inmediato, no cuando expire su token actual.
            existente.password_version = (existente.password_version or 1) + 1
            db.session.commit()
            print(f"'{args.usuario}': rol {anterior} -> sistemas. "
                  f"Sus sesiones activas quedaron invalidadas.")
            _recordatorio_2fa(existente)
            return 0

        if existente:
            print(f"ERROR: '{args.usuario}' ya existe. Usa --promover para "
                  f"cambiarle el rol.", file=sys.stderr)
            return 1

        contrasena = getpass.getpass('Contraseña para la cuenta nueva: ')
        if contrasena != getpass.getpass('Repite la contraseña: '):
            print('ERROR: las contraseñas no coinciden.', file=sys.stderr)
            return 1
        if not is_strong_password(contrasena):
            print('ERROR: contraseña débil. Mínimo 12 caracteres con mayúsculas, '
                  'minúsculas, números y símbolos, y que no sea una contraseña '
                  'común.', file=sys.stderr)
            return 1

        nuevo = User(
            username=args.usuario,
            password_hash=generate_password_hash(contrasena),
            role='sistemas',
        )
        db.session.add(nuevo)
        db.session.commit()
        print(f"Cuenta '{args.usuario}' creada con rol sistemas.")
        _recordatorio_2fa(nuevo)
        return 0


def _recordatorio_2fa(usuario) -> None:
    if usuario.totp_secret:
        return
    print()
    print('SIGUIENTE PASO OBLIGATORIO: inicia sesión con esta cuenta y activa el')
    print('2FA en Mi Perfil. El panel de sistemas no abre sin segundo factor.')


if __name__ == '__main__':
    raise SystemExit(main())
