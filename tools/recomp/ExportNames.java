import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import java.io.*;

public class ExportNames extends GhidraScript {
    public void run() throws Exception {
        File out = new File(getScriptArgs()[0]);
        if (out.isDirectory()) out = new File(out, currentProgram.getName() + ".tsv");
        try (PrintWriter w = new PrintWriter(out)) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                if (f.isExternal()) continue;
                String c = f.getComment();
                String kind = f.isThunk() ? "thunk" : (c != null && c.contains("Library: ")) ? "library" : "game";
                w.println(String.format("%08x\t%s\t%s\t%d", f.getEntryPoint().getOffset(), kind, f.getName(), f.getBody().getNumAddresses()));
            }
        }
    }
}
