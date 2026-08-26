class Package {
  constructor(namespaceName, fileLines) {
    this.namespaceName = namespaceName;
    this.fileLines = fileLines;
  }

  getItems() {
    return this.fileLines;
  }
}
module.exports = Package;
