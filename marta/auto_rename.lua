plugin {
    id = "com.erezbashan.autorename",
    name = "Auto Rename",
    apiVersion = "2.2"
}

action {
    id = "run",
    name = "Auto Rename",
    menuName = "Auto Rename",
    shortName = "Auto Rename",
    isApplicable = function(context)
        return context.activePane.model.hasActiveFiles
    end,
    apply = function(context)
        local activeFiles = context.activePane.model.activeFiles
        if not activeFiles or #activeFiles == 0 then
            return
        end

        local args = { "/Users/erezbashan/workspace/auto-rename/auto_rename.py" }
        for _, fileInfo in ipairs(activeFiles) do
            if fileInfo.path then
                table.insert(args, fileInfo.path)
            end
        end

        if #args > 1 then
            martax.execute("/opt/homebrew/bin/python3", args)
        end
    end
}

local registerContextMenu = contextMenuItemProvider or (marta and marta.contextMenuItemProvider)
registerContextMenu {
    id = "menu",
    forFiles = function(context)
        return {
            {
                actionId = "com.erezbashan.autorename.run",
                category = "mutators"
            }
        }
    end
}
