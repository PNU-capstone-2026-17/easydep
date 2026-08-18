const _ = require('lodash');
const Field = require('./Field');
const Method = require('./Method');

class Class {
  constructor(className, members, stereotype = null) {
    this.cExtends = null;
    this.members = members || [];
    this.members.forEach(member => member.setIsConstructor(this.isConstructor.bind(this)));
    this.className = className;
    this.nNamespace = null;
    this.stereotype = stereotype;
    this.basePackage = null;
  }

  static splitArrays(acc, dep) {
    // List<Value> -> [List, Value]
    const parts = dep.split('<');
    parts.forEach((part) => {
      acc.push(_.trimEnd(part, '>'));
    });
    return acc;
  }

  _getDependencies() {
    const returnTypes = this.members.map(member => member.getReturnType());
    const parameterTypes = this.members
      .reduce((acc, member) => [...acc, ...member.getParameters()], [])
      .map(params => params.getReturnType());
    const ignoreModules = ['void', 'async'];
    const all = [...returnTypes, ...parameterTypes]
      .reduce(Class.splitArrays, [])
      .filter(type => ignoreModules.indexOf(type) === -1);

    return _.uniq(all);
  }

  static get langNativeModules() {
    return {
      ecmascript6: ['EventEmitter'],
    };
  }

  getNativeModules() {
    // how to select language specific native modules..
    const nativeModules = Class.langNativeModules.ecmascript6;
    const allDeps = this._getDependencies();
    const isValid = dep => nativeModules.indexOf(dep) !== -1;
    return _.uniq(_.filter(allDeps, isValid));
  }

  get3rdPartyModules() {
    // figure out 3rd party dependencies
    const native = this.getNativeModules();
    const allDeps = this._getDependencies();
    const isValid = dep => native.indexOf(dep) === -1;
    return _.filter(allDeps, isValid);
  }

  getAppModules() {
    const native = this.getNativeModules();
    const extDep = this.get3rdPartyModules();
    const exluded = [...native, ...extDep];
    return _.without(this._getDependencies(), ...exluded);
  }

  setExtends(className) {
    this.cExtends = className;
  }

  getExtends() {
    return this.cExtends;
  }

  setNamespace(namespace) {
    this.nNamespace = namespace;
  }

  getNamespace() {
    return this.nNamespace;
  }

  setBasePackage(basePackage) {
    this.basePackage = basePackage || null;
  }

  getBasePackage() {
    return this.basePackage;
  }

  getStereotype() {
    return this.stereotype;
  }

  isBoundary() {
    return String(this.stereotype).toLowerCase() === 'boundary';
  }

  isControl() {
    return String(this.stereotype).toLowerCase() === 'control';
  }

  isGateway() {
    return String(this.stereotype).toLowerCase() === 'gateway';
  }

  isEntity() {
    return String(this.stereotype).toLowerCase() === 'entity';
  }

  isActor() {
    return String(this.stereotype).toLowerCase() === 'actor';
  }

  shouldGenerate() {
    return !this.isActor();
  }

  getJavaImports() {
    const types = this.members
      .map(member => [member.getReturnType(), ...member.getParameters().map(param => param.getReturnType())])
      .reduce((all, values) => all.concat(values), []);
    const imports = [];
    if (types.some(type => /(^|[<, ])List</.test(type) || type === 'List')) imports.push('java.util.List');
    if (this.isEntity()
      && types.some(type => /(^|[<, ])List</.test(type) || type === 'List')) {
      imports.push('java.util.ArrayList');
    }
    if (types.some(type => /(^|[<, ])Map</.test(type) || type === 'Map')) imports.push('java.util.Map');
    if (this.isEntity()
      && types.some(type => /(^|[<, ])Map</.test(type) || type === 'Map')) {
      imports.push('java.util.HashMap');
    }
    if (types.some(type => /(^|[<, ])Set</.test(type) || type === 'Set')) imports.push('java.util.Set');
    if (this.isEntity()
      && types.some(type => /(^|[<, ])Set</.test(type) || type === 'Set')) {
      imports.push('java.util.HashSet');
    }
    if (types.some(type => String(type).includes('DateTime'))) imports.push('java.time.OffsetDateTime');
    if (types.some(type => /\b(?:BigDecimal|Decimal)\b/i.test(String(type)))) {
      imports.push('java.math.BigDecimal');
    }
    return imports;
  }

  isAbstract() { // eslint-disable-line class-methods-use-this
    return false;
  }

  isInterface() { // eslint-disable-line class-methods-use-this
    return false;
  }

  getName() {
    return this.className;
  }

  isConstructor(name) {
    const languageSpecific = {
      coffeescript: 'constructor',
      ecmascript5: 'constructor',
      ecmascript6: 'constructor',
      java: this.getName(),
      php: '__construct',
      python: '__init__',
      ruby: 'initialize',
      cpp: this.getName(),
      typescript: 'constructor',
    };
    return Object.values(languageSpecific).indexOf(name) !== -1;
  }

  getConstructorArgs() {
    const methods = this.getMethods();
    const cs = methods.find(method => this.isConstructor(method.getName()));
    if (cs) {
      return cs.getParameters();
    }
    return [];
  }

  hasPublichMethods() {
    return !!this.getMethods().length;
  }

  getPublicMethods() {
    return _.filter(this.getMethods(), method => method.isPublic());
  }


  hasMethods() {
    return !!this.getMethods().length;
  }

  getPrivateMethods() {
    return _.filter(this.getMethods(), method => method.isPrivate());
  }

  hasPrivateMethods() {
    return !!this.getPrivateMethods().length;
  }

  /**
   * get methods
   * @returns {[Method>]} list of Method's
   * @private
   */
  getMethods() {
    const aResult = this.members.filter(file => file instanceof Method);
    return aResult;
  }

  hasFields() {
    return !!this.getFields().length;
  }

  hasPrivateFields() {
    return !!this.getPrivateFields().length;
  }

  getPrivateFields() {
    return _.filter(this.getFields(), field => field.isPrivate());
  }

  getFields() {
    const aResult = this.members.filter(file => (!(file instanceof Method) && (file instanceof Field)));
    return aResult;
  }

  getFullName() {
    if (this.getNamespace() !== null) {
      return `${this.getNamespace().getFullName()}.${this.getName()}`;
    }
    return this.getName();
  }

  getEntityBehaviorMethods() {
    const accessors = new Set();
    this.getFields().forEach((field) => {
      const suffix = field.getJavaAccessorSuffix();
      accessors.add(`get${suffix}`);
      accessors.add(`set${suffix}`);
      if (field.getJavaType() === 'boolean') accessors.add(`is${suffix}`);
    });
    return this.getMethods().filter(method => !accessors.has(method.getName()));
  }

  getJavaEntityMethodBody(method) {
    const name = method.getName();
    const parameters = method.getParameters();
    const fields = this.getFields();
    const fieldNames = new Set(fields.map(field => field.getName()));
    if (parameters.length === 0 && fieldNames.has(name)) {
      return `return this.${name};`;
    }
    if (name.startsWith('is') && parameters.length === 0) {
      const matchingBoolean = fields.find(
        field => field.getJavaType() === 'boolean'
          && (field.getName() === name || name === `is${field.getJavaAccessorSuffix()}`),
      );
      if (matchingBoolean) return `return this.${matchingBoolean.getName()};`;
    }
    if (name.startsWith('set') && parameters.length === 1) {
      const stem = name.slice(3).toLowerCase();
      const parameter = parameters[0];
      const target = fields.find(
        field => field.getName().toLowerCase() === stem
          || field.getName().toLowerCase().startsWith(stem)
          || field.getName().toLowerCase() === parameter.getName().toLowerCase(),
      );
      if (target) return `this.${target.getName()} = ${parameter.getName()};`;
    }
    if (name === 'getValue' && fieldNames.has('quantity') && parameters.length === 1) {
      return `return this.quantity * ${parameters[0].getName()};`;
    }
    if (name === 'isSuccessful' && fieldNames.has('status')) {
      return 'return "completed".equalsIgnoreCase(this.status);';
    }
    const mark = name.match(/^markAs([A-Z].*)$/);
    if (mark && fieldNames.has('status')) {
      return `this.status = "${mark[1].toLowerCase()}";`;
    }
    const add = name.match(/^add([A-Z].*)$/);
    if (add && parameters.length === 1) {
      const singular = add[1].charAt(0).toLowerCase() + add[1].slice(1);
      const plural = `${singular}s`;
      if (fieldNames.has(plural)) return `this.${plural}.add(${parameters[0].getName()});`;
    }
    if (name === 'toString' && parameters.length === 0) {
      const values = fields.map(field => `"${field.getName()}=" + ${field.getName()}`);
      return `return "${this.getName()}{" + ${values.join(' + ", " + ')} + '}';`;
    }
    throw new Error(`No executable Java entity strategy for ${this.getName()}.${name}`);
  }

  getOutputPath() {
    const prefix = this.basePackage ? `${this.basePackage.replace(/\./g, '/')}/` : '';
    return `${prefix}${this.getName()}.java`;
  }
}
module.exports = Class;
