from atlas.ingest.parsers import magicbricks

# parser key -> (parse function, version stamp)
PARSERS = {
    "magicbricks": (magicbricks.parse, magicbricks.PARSER_VERSION),
}
