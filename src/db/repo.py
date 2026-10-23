import sqlite3
class repoManager:
    def __init__(self, db_name="inventory.db"):
        # The connection belongs to the object instance
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_table()
    def add_book():
        pass
        # TODO: Write function to add row to book table
    def set_status():
        pass
        # TODO: write function that update value status in book table
    def list_by_status(status):
        pass
        # return list of status matching passed status param