const Class = require('./Class');

/** A PlantUML enumeration with its literal members preserved for Java output. */
class Enum extends Class {
  constructor(className, values, stereotype = null) {
    super(className, [], stereotype || 'Enumeration');
    this.values = values || [];
  }

  isEnumeration() { // eslint-disable-line class-methods-use-this
    return true;
  }

  getValues() {
    return this.values;
  }
}

module.exports = Enum;
