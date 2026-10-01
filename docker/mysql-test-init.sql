-- Runs when mysql-test starts (its data lives in memory, so every start is a
-- fresh server). Lets the test user make and use terraforma_test_gw0, _gw1, ...:
-- one database per worker in a parallel run (pytest -n auto).
GRANT ALL PRIVILEGES ON `terraforma\_test\_%`.* TO 'terraforma'@'%';
FLUSH PRIVILEGES;
