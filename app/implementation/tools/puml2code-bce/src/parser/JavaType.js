/** Convert the closed BCE design type vocabulary to compilable Java types. */
function toJavaType(value) {
  return String(value)
    .replace(/\b(?:array|collection|list|page)\s*</gi, 'List<')
    .replace(/\biterable\s*</gi, 'Iterable<')
    .replace(/\bmap\s*</gi, 'Map<')
    .replace(/\boptional\s*</gi, 'Optional<')
    .replace(/\bset\s*</gi, 'Set<')
    .replace(/\bbiginteger\b/gi, 'BigInteger')
    .replace(/\b(?:bigdecimal|decimal|number)\b/gi, 'BigDecimal')
    .replace(/\b(?:uuid|guid)\b/gi, 'UUID')
    .replace(/\blocaldatetime\b/gi, 'LocalDateTime')
    .replace(/\b(?:offsetdatetime|datetime)\b/gi, 'OffsetDateTime')
    .replace(/\b(?:instant|timestamp)\b/gi, 'Instant')
    .replace(/\blocaldate\b/gi, 'LocalDate')
    .replace(/\bdate\b/gi, 'LocalDate')
    .replace(/\b(?:localtime|time)\b/gi, 'LocalTime')
    .replace(/\b(?:string|str)\b/gi, 'String')
    .replace(/\binteger\b/gi, 'int')
    .replace(/\bfloat\b/gi, 'double')
    .replace(/\bbool\b/gi, 'boolean')
    .replace(/\bcharacter\b/gi, 'char')
    .replace(/\b(?:any|object)\b/gi, 'Object');
}

module.exports = toJavaType;
