import pytest
from vision.barcode import decode_isbn, is_valid_isbn13

# List your 3 test images here
@pytest.mark.parametrize("image_name", [
    "barcode1.jpeg",
    "barcode2.jpeg",
    "barcode3.jpeg"
])
def test_decode_isbn_success(image_name):
    """
    Test that each barcode image successfully decodes into a valid 13-digit ISBN.
    """
    isbn = decode_isbn(image_name)

    # Assert that a result was found
    assert isbn is not None, f"Failed to decode any valid ISBN from {image_name}"

    # Verify strict 13-digit structure and ISBN-13 checksum validity
    assert len(isbn) == 13, f"Decoded code '{isbn}' from {image_name} is not 13 digits long"
    assert isbn.isdigit(), f"Decoded code '{isbn}' from {image_name} contains non-digit characters"
    assert is_valid_isbn13(isbn), f"Decoded code '{isbn}' from {image_name} failed ISBN-13 checksum validation"