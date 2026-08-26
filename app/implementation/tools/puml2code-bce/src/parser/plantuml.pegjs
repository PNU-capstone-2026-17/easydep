plantumlfile
  = items:((noise newline { return null }) / (noise "@startuml" noise newline filelines:umllines noise "@enduml" noise { var UMLBlock = require("./UMLBlock"); return new UMLBlock(filelines) }))* { for (var i = 0; i < items.length; i++) { if (items[i] === null) { items.splice(i, 1); i--; } } return items }
umllines
  = lines:(umlline*) { for (var i = 0; i < lines.length; i++) { if (lines[i]===null) { lines.splice(i, 1); i--; } } return lines; }
umlline
  = propertyset newline { return null }
  / titleset newline { return null }
  / headerset newline { return null }
  / noise newline { return null }
  / commentline { return null }
  / noteline { return null }
  / hideline newline { return null }
  / skinparams newline { return null }
  / directive newline { return null }
  / declaration:packagedeclaration newline { return declaration }
  / declaration:namespacedeclaration newline { return declaration }
  / declaration:classdeclaration newline { return declaration }
  / declaration:enumdeclaration newline { return declaration }
  / declaration:abstractclassdeclaration newline { return declaration }
  / declaration:interfacedeclaration newline { return declaration }
  / declaration:memberdeclaration newline { return declaration }
  / declaration:connectordeclaration newline { return declaration }
hideline
  = noise "hide empty members" noise
skinparams
  = noise "skinparam" noise [^\r\n]+
directive
  = noise "allowmixing" noise
  / noise "!theme" noise [^\r\n]+
connectordeclaration
  = noise leftObject:objectname noise connectordescription? noise connector:connectortype noise connectordescription? noise rightObject:objectname noise ([:] [^\r\n]+)? { var Connection = require("./Connection"); return new Connection(leftObject, connector, rightObject) }
connectordescription
  = noise ["]([\\]["]/[^"])*["] noise
titleset
  = noise "title " noise [^\r\n]+ noise
headerset
  = "header" (!"endheader" .)* "endheader"
commentline
  = noise "'" [^\r\n]+ noise
  / noise ".." [^\r\n\.]+ ".." noise
  / noise "--" [^\r\n\-]+ "--" noise
  / noise "__" [^\r\n\_]+ "__" noise
noteline
  = noise "note " noise [^\r\n]+ noise
connectortype
  = item:extends { return item }
  / concatenates { var Composition = require("./Composition"); return new Composition() }
  / aggregates { var Aggregation = require("./Aggregation"); return new Aggregation() }
  / directional { return null }
  / connectorsize { return null }
directional
  = connectorsize ">"
  / "<" connectorsize
extends
  = "<|" connectorsize { var Extension = require("./Extension"); return new Extension(true) }
  / connectorsize "|>" { var Extension = require("./Extension"); return new Extension(false) }
connectorsize
  = ".."
  / [-]+ "up" [-]+
  / [-]+ "down" [-]+
  / [-]+ "left" [-]+
  / [-]+ "right" [-]+
  / "---"
  / "--"
  / [.]
  / [-]
concatenates
  = "*" connectorsize
  / connectorsize [*]
aggregates
  = "o" connectorsize
  / connectorsize [o]
startblock
  = noise [{] noise
endblock
  = noise [}]
propertyset
  = "setpropname.*"
packagedeclaration
  = "package " name:packagename startblock newline lines:umllines endblock { var Package = require("./Package"); return new Package(name, lines); }
  / "package " name:packagename newline lines:umllines "end package" { var Package = require("./Package"); return new Package(name, lines); }
packagename
  = name:objectname { return name; }
  / '"' name:[^"]* '"' { return name.join(""); }
interfacedeclaration
  = noise "interface " noise classname:objectname noise startblock lines:umllines endblock { var InterfaceClass = require("./InterfaceClass"); return new InterfaceClass(classname, lines) }
abstractclassdeclaration
  = noise "abstract " noise "class "? noise classname:objectname noise startblock lines:umllines endblock { var AbstractClass = require("./AbstractClass"); return new AbstractClass(classname, lines) }
  / noise "abstract " noise "class "? noise classname:objectname noise { var AbstractClass = require("./AbstractClass"); return new AbstractClass(classname) }
  / noise "abstract " noise "class "? noise classname:objectname noise newline noise lines:umllines "end class" { var AbstractClass = require("./AbstractClass"); return new AbstractClass(classname, lines) }
noise
  = [ \t]*
splitter
  = [:]
newline
  = [\r\n]
  / [\n]
classdeclaration
  = noise "class " noise classname:objectname stereotype:stereotype? noise startblock lines:umllines endblock { var Class = require("./Class"); return new Class(classname, lines, stereotype) }
  / noise "class " noise classname:objectname stereotype:stereotype noise { var Class = require("./Class"); return new Class(classname, [], stereotype) }
  / noise "class " noise classname:objectname noise { var Class = require("./Class"); return new Class(classname) }
  / noise "class " noise classname:objectname noise newline noise lines:umllines "end class" { var Class = require("./Class"); return new Class(classname, lines) }
enumdeclaration
  = noise "enum " noise classname:objectname stereotype:stereotype? noise startblock values:enumvalues endblock { var Enum = require("./Enum"); return new Enum(classname, values, stereotype); }
  / noise "enum " noise classname:objectname stereotype:stereotype? noise { var Enum = require("./Enum"); return new Enum(classname, [], stereotype); }
enumvalues
  = values:(enumspace value:membername enumspace [,]? { return value; })* { return values; }
enumspace
  = [ \t\r\n]*
color
  = [#][0-9a-fA-F]+
namespacedeclaration
  = noise "namespace " noise namespacename:objectname noise color? noise startblock lines:umllines endblock { var Namespace = require("./Namespace"); return new Namespace(namespacename, lines) }
  / noise "namespace " noise namespacename:objectname noise newline umllines "end namespace" { var Namespace = require("./Namespace"); return new Namespace(namespacename) }
staticmemberdeclaration
  = "static " memberdeclaration
memberdeclaration
  = declaration:bcemethoddeclaration { return declaration }
  / declaration:bcefielddeclaration { return declaration }
  / declaration:datatypefielddeclaration { return declaration }
  / declaration:methoddeclaration { return declaration }
  / declaration:fielddeclaration { return declaration }
bcefielddeclaration
  = noise accessortype:accessortype noise membername:membername noise ":" noise datatype:javatype noise { var Field = require("./Field"); return new Field(accessortype, datatype, membername, false) }
datatypefielddeclaration
  = noise membername:membername noise ":" noise datatype:javatype noise { var Field = require("./Field"); return new Field("-", datatype, membername, false) }
bcemethoddeclaration
  = noise accessortype:accessortype noise methodname:membername noise "(" parameters:bceparameters? ")" returntype:bcereturntype? noise { var Method = require("./Method"); return new Method(accessortype, returntype || "void", methodname, parameters || []); }
bceparameters
  = first:bceparameter rest:(noise "," noise bceparameter)* { return [first].concat(rest.map(function(item) { return item[3]; })); }
bceparameter
  = name:membername noise ":" noise datatype:javatype { var Parameter = require("./Parameter"); return new Parameter(datatype, name); }
bcereturntype
  = noise ":" noise datatype:javatype { return datatype; }
javatype
  = base:objectname generic:genericargs? arrays:("[]"*) { return base + (generic || "") + arrays.join(""); }
genericargs
  = "<" noise first:javatype rest:(noise "," noise javatype)* noise ">" { return "<" + [first].concat(rest.map(function(item) { return item[3]; })).join(", ") + ">"; }
stereotype
  = noise "<<" noise value:[^>]+ noise ">>" { return value.join("").trim(); }
fielddeclaration
  = noise accessortype:accessortype noise abstract:abstract? returntype:returntype noise membername:membername noise { var Field = require("./Field"); return new Field(accessortype, returntype, membername, abstract) }
  / noise accessortype:accessortype noise abstract:abstract? membername:membername noise { var Field = require("./Field"); return new Field(accessortype, "void", membername, abstract) }
  / noise returntype:returntype noise abstract:abstract? membername:membername noise { var Field = require("./Field"); return new Field("+", returntype, membername, abstract) }
methoddeclaration
  = noise field:fielddeclaration [(] parameters:methodparameters [)] noise { var Method = require("./Method"); return new Method(field.getAccessType(), field.getReturnType(), field.getName(), parameters); }
methodparameters
  = items:methodparameter* { return items; }
methodparameter
  = noise datatype:returntype membername:([ ] membername) [=] defaultValue:(defaultvalue) [,]? { var Parameter = require("./Parameter"); return new Parameter(datatype, membername[1], defaultValue); }
  / noise datatype:returntype membername:([ ] membername) [,]? { var Parameter = require("./Parameter"); return new Parameter(datatype, membername[1]); }
  / noise membername:membername [,]? { var Parameter = require("./Parameter"); return new Parameter("Object", membername); }
returntype
  = items:[^ ,\n\r\t(){}<>]+ template:([<] templateargs [>])? typeinfo:[*\[\]&]* { return items.join("") + (template ? template.join("") : "") + typeinfo.join(""); }
templateargs
  = items:templatearg+ { return items; }
templatearg
  = noise item:returntype noise [,]? { return item; }
objectname
  = objectname:([A-Za-z_][A-Za-z0-9.]*) { return [objectname[0], objectname[1].join("")].join("") }
membername
  = "operator" op:[+=*/<>!~^&|,\[\]]+ { return "operator" + op.join("") }
  / items:([A-Za-z_\*][A-Za-z0-9_]*) { return [items[0], items[1].join("")].join("")}
defaultvalue
  = items:([{}\[\]A-Za-z0-9_\'\"]*) { return items.join("") }
accessortype
  = publicaccessor
  / privateaccessor
  / protectedaccessor
publicaccessor
  = [+]
privateaccessor
  = [-]
protectedaccessor
  = [#]
abstract
  = abstract:("{abstract}") noise { return !!abstract; }
