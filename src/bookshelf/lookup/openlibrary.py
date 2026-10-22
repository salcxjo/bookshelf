import requests
def fetch_by_isbn(isbn: str) -> dict | None:

    url = "https://openlibrary.org/isbn/"
    response = requests.get(url+isbn+".json")

    # Parse response into a Python dictionary
    data = response.json()

    # Extract a specific key
    book_title = data["title"]
    print(book_title)
