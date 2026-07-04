import bcrypt


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


if __name__ == "__main__":
    password = input("Enter password: ")

    hashed_password = hash_password(password)

    print("\nHashed password:")
    print(hashed_password)