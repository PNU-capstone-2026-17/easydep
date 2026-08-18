class Parameter {
  constructor(returnType, memberName, defaultValue) {
    this.sReturnType = returnType;
    this.sParameterName = memberName;
    this.sDefaultValue = defaultValue;
  }

  getDefaultValue() {
    return this.sDefaultValue;
  }

  getReturnType() {
    return this.sReturnType;
  }

  getJavaType() {
    return String(this.sReturnType)
      .replace(/\bstring\b/gi, 'String')
      .replace(/\bdatetime\b/gi, 'OffsetDateTime')
      .replace(/\bfloat\b/gi, 'double')
      // Legacy BCE diagrams can still contain the old non-Java alias.
      .replace(/\bdecimal\b/gi, 'BigDecimal');
  }

  getName() {
    return this.sParameterName;
  }
}
module.exports = Parameter;
