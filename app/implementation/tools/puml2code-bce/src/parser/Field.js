class Field {
  constructor(accessType, returnType, fieldName, abstract) {
    this.sAccessType = accessType;
    this.sReturnType = returnType;
    this.sFieldName = fieldName;
    this.bInterface = false;
    this.bAbstract = !!abstract;
    this.isConstructor = undefined;
  }

  isPrivate() {
    return this.getAccessType() === '-';
  }

  isProtected() {
    return this.getAccessType() === '#';
  }

  isPublic() {
    return this.getAccessType() === '+';
  }

  setIsConstructor(isConstructor) {
    this.isConstructor = isConstructor;
  }

  isNotConstructor() {
    return !this.isConstructor(this.getName());
  }

  setInterface() {
    this.bInterface = true;
  }

  isAbstract() {
    return this.bAbstract;
  }

  isInterface() {
    return this.bInterface;
  }

  getAccessType() {
    return this.sAccessType;
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
    return this.sFieldName;
  }

  getJavaAccessorSuffix() {
    return this.sFieldName.charAt(0).toUpperCase() + this.sFieldName.slice(1);
  }

  getJavaInitializer() {
    const type = this.getJavaType();
    if (type.startsWith('List<')) return ' = new ArrayList<>()';
    if (type.startsWith('Map<')) return ' = new HashMap<>()';
    if (type.startsWith('Set<')) return ' = new HashSet<>()';
    return '';
  }

  getParameters() { // eslint-disable-line class-methods-use-this
    return [];
  }
}
module.exports = Field;
